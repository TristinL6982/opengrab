"""Shared Mega protocol code for the mega and transferit plugins.

Mega encrypts every file on the client. A public link carries the key in
the URL fragment; the storage servers only ever see encrypted bytes. The
flow, using the same public API requests the official web client makes:

  1. ``g`` command on https://g.api.mega.co.nz/cs: size, encrypted
     attributes (the file name) and a temporary download URL.
  2. The encrypted bytes are downloaded like any other file.
  3. ``finalize`` decrypts them (AES-128-CTR) and checks Mega's chunk MAC.

Key layout (32-bit big-endian words, "a32"): a file key is 8 words
k0..k7. The AES key is (k0^k4, k1^k5, k2^k6, k3^k7), the CTR nonce is
(k4, k5) and the expected "meta MAC" is (k6, k7). Folder keys are 4 words;
they decrypt the node keys of the folder's contents with AES-ECB.

The module name starts with an underscore so the plugin registry does not
scan it; the two plugin modules import it.
"""
from __future__ import annotations

import base64
import json
import os
import random
import struct
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from Cryptodome.Cipher import AES

from ..base import PluginError

API_URL = "https://g.api.mega.co.nz/cs"

# ---------------------------------------------------------------------------
# API error codes (from the public SDK's error table)
# ---------------------------------------------------------------------------
ERRORS = {
    -1: "Mega internal error",
    -2: "Mega rejected the request (malformed link?)",
    -3: "Mega is busy, try again shortly",
    -4: "Mega rate limit reached, try again later",
    -5: "Mega request failed",
    -6: "too many requests to Mega, try again later",
    -7: "Mega request out of range",
    -8: "the link has expired",
    -9: "file or folder not found (deleted, or the link is wrong)",
    -10: "Mega reported a circular link",
    -11: "access denied (the link is private or was disabled)",
    -12: "Mega object already exists",
    -13: "Mega request incomplete",
    -14: "the decryption key in the link is wrong, or a password is required",
    -15: "Mega session invalid",
    -16: "the file or folder was blocked or taken down by Mega",
    -17: "Mega transfer quota exceeded",
    -18: "the resource is temporarily unavailable on Mega, try again later",
    -19: "too many connections to Mega",
    -24: "this download would exceed the Mega transfer quota",
}
EAGAIN = -3
RETRYABLE = {-3, -4, -6}
EOVERQUOTA = -17
EGOINGOVERQUOTA = -24


class MegaApiError(PluginError):
    def __init__(self, code: int, wait_s: Optional[int] = None):
        self.code = code
        self.wait_s = wait_s
        msg = ERRORS.get(code, f"Mega API error {code}")
        if code in (EOVERQUOTA, EGOINGOVERQUOTA) or wait_s:
            msg = quota_message(wait_s)
        super().__init__(f"{msg} (code {code})")


def quota_message(wait_s: Optional[int]) -> str:
    base = "Mega transfer quota exceeded"
    if wait_s and wait_s > 0:
        # "wait N minutes" first: the engine reads the first number + unit
        mins = (int(wait_s) + 59) // 60
        when = f"wait {mins} minutes"
        if mins >= 60:
            h, m = divmod(mins, 60)
            when += f" (about {h} h {m} min)" if m else f" (about {h} h)"
        return f"{base}; Mega asks to {when} before downloading again"
    return f"{base}; wait for the quota to reset (usually within a few hours)"


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------
def b64url_decode(s: str) -> bytes:
    s = s.strip().replace("-", "+").replace("_", "/").replace(",", "")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=False)
    except Exception as e:
        raise PluginError(f"invalid base64 in Mega data: {e}") from e


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def bytes_to_a32(b: bytes) -> list[int]:
    if len(b) % 4:
        b += b"\0" * (4 - len(b) % 4)
    return list(struct.unpack(f">{len(b) // 4}I", b))


def a32_to_bytes(a: Iterable[int]) -> bytes:
    a = list(a)
    return struct.pack(f">{len(a)}I", *a)


def b64_to_a32(s: str) -> list[int]:
    return bytes_to_a32(b64url_decode(s))


def unpack_file_key(k: list[int]) -> tuple[bytes, bytes, list[int]]:
    """(aes_key16, nonce8, meta_mac[2]) from an 8-word file key."""
    if len(k) != 8:
        raise PluginError("invalid Mega file key (expected 256 bits)")
    key = a32_to_bytes([k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7]])
    nonce = a32_to_bytes(k[4:6])
    return key, nonce, [k[6], k[7]]


def node_aes_key(k: list[int]) -> bytes:
    """AES key for a node's attributes: files fold 8 words, folders use 4."""
    if len(k) == 8:
        return unpack_file_key(k)[0]
    if len(k) == 4:
        return a32_to_bytes(k)
    raise PluginError("invalid Mega node key length")


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------
def decrypt_attr(at_b64: str, aes_key: bytes) -> dict:
    """Decrypt an ``at`` blob: AES-CBC, zero IV, ``MEGA{json}`` padded."""
    data = b64url_decode(at_b64)
    if not data or len(data) % 16:
        raise PluginError("invalid Mega attribute block")
    plain = AES.new(aes_key, AES.MODE_CBC, b"\0" * 16).decrypt(data)
    plain = plain.rstrip(b"\0")
    if not plain.startswith(b"MEGA{"):
        raise PluginError(
            "could not decrypt the file attributes: the key in the link is wrong")
    try:
        return json.loads(plain[4:].decode("utf-8", errors="replace"))
    except ValueError as e:
        raise PluginError(f"bad Mega attributes: {e}") from e


def encrypt_attr(attrs: dict, aes_key: bytes) -> str:
    """Inverse of decrypt_attr (used by tests and fixtures)."""
    raw = b"MEGA" + json.dumps(attrs, separators=(",", ":")).encode("utf-8")
    raw += b"\0" * (-len(raw) % 16)
    return b64url_encode(AES.new(aes_key, AES.MODE_CBC, b"\0" * 16).encrypt(raw))


def decrypt_key(enc: list[int], master: list[int]) -> list[int]:
    """Decrypt a node key with a 4-word key (AES-ECB, block by block)."""
    if len(enc) not in (4, 8):
        raise PluginError("invalid encrypted Mega node key")
    ecb = AES.new(a32_to_bytes(master), AES.MODE_ECB)
    return bytes_to_a32(ecb.decrypt(a32_to_bytes(enc)))


def encrypt_key(plain: list[int], master: list[int]) -> list[int]:
    ecb = AES.new(a32_to_bytes(master), AES.MODE_ECB)
    return bytes_to_a32(ecb.encrypt(a32_to_bytes(plain)))


def safe_name(name: str, fallback: str) -> str:
    name = (name or "").replace("\0", "").strip()
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    name = name.strip(". ")
    return name or fallback


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
class MegaApi:
    """Minimal client for the public ``cs`` endpoint over ctx.client."""

    def __init__(self, client, sleep: Callable[[float], None] = time.sleep,
                 retries: int = 4, api_url: str = API_URL):
        self.client = client
        self.sleep = sleep
        self.retries = retries
        self.api_url = api_url
        self._seq = random.randint(0, 0xFFFFFFFF)

    def call(self, cmd: dict, **params) -> object:
        """Send one command; returns its result or raises MegaApiError."""
        delay = 1.0
        for attempt in range(self.retries + 1):
            self._seq += 1
            q = "&".join([f"id={self._seq}"] +
                         [f"{k}={v}" for k, v in params.items() if v])
            try:
                r = self.client.post(f"{self.api_url}?{q}", json_body=[cmd],
                                     detect_challenges=False)
            except TypeError:  # simple fakes without the keyword
                r = self.client.post(f"{self.api_url}?{q}", json_body=[cmd])
            status = getattr(r, "status_code", 200)
            if status >= 500 or status == 429:
                res: object = EAGAIN
            elif status >= 400:
                raise PluginError(f"Mega API returned HTTP {status}")
            else:
                try:
                    res = r.json()
                except ValueError:
                    raise PluginError("Mega API returned an unreadable reply")
            if isinstance(res, list):
                res = res[0] if res else -1
            if isinstance(res, int) and res < 0:
                if res in RETRYABLE and attempt < self.retries:
                    self.sleep(delay)
                    delay = min(delay * 2, 16.0)
                    continue
                raise MegaApiError(res)
            if isinstance(res, dict) and isinstance(res.get("e"), int) \
                    and res["e"] < 0:
                raise MegaApiError(res["e"], _int(res.get("tl")))
            return res
        raise MegaApiError(EAGAIN)

    # -- commands --------------------------------------------------------
    def file_info(self, *, p: str = "", node: str = "", want_url: bool,
                  query: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
        """``g`` command for a public file handle (p) or a node inside a
        shared folder/transfer (node, with the share in ``query``)."""
        cmd: dict = {"a": "g"}
        if p:
            cmd["p"] = p
        else:
            cmd["n"] = node
        if want_url:
            cmd["g"] = 1
            cmd["ssl"] = 1
        cmd.update(extra or {})
        res = self.call(cmd, **(query or {}))
        if not isinstance(res, dict):
            raise PluginError("unexpected Mega reply to a file request")
        return res

    def list_nodes(self, query: dict, ca: bool = True) -> list[dict]:
        cmd: dict = {"a": "f", "c": 1, "r": 1}
        if ca:
            cmd["ca"] = 1
        res = self.call(cmd, **query)
        if not isinstance(res, dict) or not isinstance(res.get("f"), list):
            raise PluginError("unexpected Mega reply to a folder listing")
        return res["f"]


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def download_url(info: dict) -> str:
    """Pick the download URL out of a ``g`` reply, mapping quota errors."""
    tl = _int(info.get("tl"))
    g = info.get("g")
    if isinstance(g, list):
        raise PluginError("this Mega file is stored in CloudRAID parts, "
                          "which OpenGrab does not download yet")
    if not isinstance(g, str) or not g.startswith("http"):
        if tl:
            raise MegaApiError(EOVERQUOTA, tl)
        raise PluginError("Mega gave no download URL for this file")
    return g


# ---------------------------------------------------------------------------
# Folder listings
# ---------------------------------------------------------------------------
def node_key(node: dict, folder_key: list[int], root: str = "") -> list[int]:
    """Decrypt ``node['k']`` (``owner:key[/owner:key...]``) with the folder key."""
    raw = str(node.get("k") or "")
    parts = [p for p in raw.split("/") if ":" in p]
    if not parts:
        raise PluginError("Mega node has no key")
    pick = None
    for p in parts:
        owner, _, k = p.partition(":")
        if root and owner == root:
            pick = k
            break
    if pick is None:
        pick = parts[0].partition(":")[2]
    enc = b64_to_a32(pick)
    if len(enc) not in (4, 8):
        raise PluginError("Mega node key has an unexpected size")
    return decrypt_key(enc, folder_key)


def decode_nodes(nodes: list[dict], folder_key: list[int],
                 root: str = "") -> dict[str, dict]:
    """Decrypt a folder listing into {handle: {h,p,t,s,name,key}}.

    Nodes whose key or attributes cannot be decrypted are skipped (Mega
    lists such nodes as "undecryptable" too)."""
    out: dict[str, dict] = {}
    for n in nodes:
        h = n.get("h")
        t = n.get("t")
        if not h or t not in (0, 1):
            continue
        try:
            k = node_key(n, folder_key, root)
            attrs = decrypt_attr(n.get("a", ""), node_aes_key(k))
        except PluginError:
            continue
        out[h] = {"h": h, "p": n.get("p", ""), "t": t,
                  "s": _int(n.get("s")) or 0,
                  "name": str(attrs.get("n") or h), "key": k}
    return out


def decode_plain_nodes(nodes: list[dict]) -> dict[str, dict]:
    """transfer.it listings: node keys arrive unwrapped (``k`` is the key)."""
    out: dict[str, dict] = {}
    for n in nodes:
        h = n.get("h")
        t = n.get("t")
        if not h or t not in (0, 1):
            continue
        try:
            raw = str(n.get("k") or "")
            k = b64_to_a32(raw.rpartition(":")[2])
            attrs = decrypt_attr(n.get("a", ""), node_aes_key(k))
        except PluginError:
            continue
        out[h] = {"h": h, "p": n.get("p", ""), "t": t,
                  "s": _int(n.get("s")) or 0,
                  "name": str(attrs.get("n") or h), "key": k}
    return out


def find_roots(tree: dict[str, dict]) -> list[str]:
    """Handles of nodes whose parent is not in the listing (the shared roots)."""
    return [h for h, n in tree.items() if n["p"] not in tree]


def files_under(tree: dict[str, dict], top: str = "") -> list[dict]:
    """Files below ``top`` (default: the listing's root folders), each with a
    ``path`` relative to it. Sorted by path so packages list predictably."""
    tops = {top} if top else {h for h in find_roots(tree) if tree[h]["t"] == 1}
    out = []
    for n in tree.values():
        if n["t"] != 0:
            continue
        parts: list[str] = []
        seen = set()
        cur = tree.get(n["p"])
        inside = n["h"] in tops or (not top and n["p"] not in tree)
        while cur is not None and cur["h"] not in seen:
            seen.add(cur["h"])
            if cur["h"] in tops:
                inside = True
                break
            parts.insert(0, cur["name"])
            cur = tree.get(cur["p"])
        if not inside:
            continue
        out.append({**n, "path": "/".join(parts + [n["name"]])})
    out.sort(key=lambda n: n["path"].lower())
    return out


class TTLCache:
    """Tiny cache so per-file children of a big folder do not each re-list it."""

    def __init__(self, ttl: float = 600.0, size: int = 16):
        self.ttl = ttl
        self.size = size
        self._d: dict = {}

    def get(self, key):
        v = self._d.get(key)
        if v and time.monotonic() - v[0] < self.ttl:
            return v[1]
        self._d.pop(key, None)
        return None

    def put(self, key, value):
        if len(self._d) >= self.size:
            self._d.pop(next(iter(self._d)))
        self._d[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# File decryption and MAC
# ---------------------------------------------------------------------------
def chunk_bounds(size: int) -> list[tuple[int, int]]:
    """Mega MAC chunks: 128 KiB, 256 KiB, ... 1 MiB, then 1 MiB each."""
    out = []
    pos = 0
    step = 1
    while pos < size:
        n = min(step * 0x20000, 0x100000, size - pos)
        out.append((pos, n))
        pos += n
        if step < 8:
            step += 1
    return out


def meta_mac_stream(chunks: Iterable[bytes], key: bytes, nonce: bytes) -> list[int]:
    """Condensed MAC over plaintext chunks laid out by chunk_bounds."""
    ecb = AES.new(key, AES.MODE_ECB)
    iv = nonce + nonce
    mac = b"\0" * 16
    for chunk in chunks:
        pad = chunk + b"\0" * (-len(chunk) % 16)
        cmac = AES.new(key, AES.MODE_CBC, iv).encrypt(pad)[-16:]
        mac = ecb.encrypt(bytes(a ^ b for a, b in zip(mac, cmac)))
    m = bytes_to_a32(mac)
    return [m[0] ^ m[1], m[2] ^ m[3]]


def ctr_cipher(key: bytes, nonce: bytes, offset: int = 0):
    if offset % 16:
        raise ValueError("CTR offset must be block aligned")
    return AES.new(key, AES.MODE_CTR, nonce=nonce, initial_value=offset // 16)


def encrypt_bytes(plain: bytes, file_key: list[int]) -> bytes:
    """Encrypt like a Mega upload (tests and fixtures)."""
    key, nonce, _ = unpack_file_key(file_key)
    return ctr_cipher(key, nonce).encrypt(plain)


def make_file_key(plain: bytes, rand: Callable[[int], bytes] = os.urandom) -> list[int]:
    """Build a valid 8-word file key for ``plain`` (fixtures)."""
    key = rand(16)
    nonce = rand(8)
    chunks = [plain[o:o + n] for o, n in chunk_bounds(len(plain))]
    mm = meta_mac_stream(chunks, key, nonce)
    k = bytes_to_a32(key)
    n = bytes_to_a32(nonce)
    v = n + mm
    return [k[0] ^ v[0], k[1] ^ v[1], k[2] ^ v[2], k[3] ^ v[3]] + v


def decrypt_file(src: Path, dst: Path, file_key: list[int],
                 verify_mac: bool = True) -> None:
    """Decrypt ``src`` into ``dst`` chunk by chunk; raises on a MAC mismatch."""
    key, nonce, expected = unpack_file_key(file_key)
    size = src.stat().st_size
    cipher = ctr_cipher(key, nonce)
    ecb = AES.new(key, AES.MODE_ECB)
    iv = nonce + nonce
    mac = b"\0" * 16
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for _off, n in chunk_bounds(size):
            enc = fi.read(n)
            if len(enc) != n:
                raise PluginError("encrypted Mega download is truncated")
            plain = cipher.decrypt(enc)
            fo.write(plain)
            if verify_mac:
                pad = plain + b"\0" * (-len(plain) % 16)
                cmac = AES.new(key, AES.MODE_CBC, iv).encrypt(pad)[-16:]
                mac = ecb.encrypt(bytes(a ^ b for a, b in zip(mac, cmac)))
    if verify_mac and size:
        m = bytes_to_a32(mac)
        if [m[0] ^ m[1], m[2] ^ m[3]] != expected:
            raise PluginError("Mega file MAC mismatch: the download is corrupt "
                              "or the key is wrong")


def finalize_download(path, link, extra_key: str = "mega") -> Path:
    """Shared finalize(): decrypt ``path`` in place using link.extra[extra_key].

    The decrypted file replaces the encrypted one under the same name. A
    MAC failure removes both, so a retry downloads the file again."""
    info = (getattr(link, "extra", None) or {}).get(extra_key) or {}
    key_b64 = info.get("key")
    if not key_b64:
        return Path(path)  # nothing to decrypt (not a Mega link)
    src = Path(path)
    if not src.is_file():
        raise PluginError(f"downloaded file is missing: {src.name}")
    want = _int(info.get("size"))
    have = src.stat().st_size
    if want is not None and have != want:
        raise PluginError(f"Mega download size mismatch ({have} of {want} bytes)")
    tmp = src.with_name(src.name + ".megadec")
    try:
        decrypt_file(src, tmp, b64_to_a32(key_b64),
                     verify_mac=bool(info.get("verify_mac", True)))
    except PluginError:
        for p in (tmp, src):
            try:
                p.unlink()
            except OSError:
                pass
        raise
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, src)
    return src
