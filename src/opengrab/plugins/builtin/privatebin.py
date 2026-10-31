"""PrivateBin pastes: encrypted link lists -> one child link per download link.

PrivateBin (open-source, privatebin.info) encrypts every paste in the
browser; the key is the part of the URL after ``#`` and never reaches the
server. OpenGrab does what PrivateBin's own page does: it asks the server
for the encrypted paste (JSON API, format v2), derives the key with
PBKDF2-SHA256, decrypts with AES-256-GCM, inflates it, and returns every
link in the text as its own download. Password-protected pastes need the
password saved as an account for the paste's site.

Any host qualifies when the URL looks like a PrivateBin link:
``https://<host>/?<16 hex chars>#<base58 key>``.
"""
from __future__ import annotations

import base64
import json
import re
import zlib
from urllib.parse import urlparse

from ..base import LoginRequired, PluginContext, PluginError, ResolveResult, SitePlugin
from .pastes import extract_links

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ID_RE = re.compile(r"^(?:pasteid=)?([0-9a-f]{16})$")


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = B58.find(ch)
        if i < 0:
            raise ValueError("not base58")
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + raw


def parse(url: str) -> tuple[str, str, str] | None:
    """(base url, paste id, key fragment) for PrivateBin-shaped links."""
    p = urlparse(url.strip())
    m = ID_RE.match(p.query or "")
    frag = (p.fragment or "").lstrip("-")  # "-" marks "ask before loading" in PrivateBin
    if p.scheme not in ("http", "https") or not m or not re.fullmatch(f"[{B58}]{{30,60}}", frag):
        return None
    return f"{p.scheme}://{p.netloc}{p.path or '/'}", m.group(1), frag


def decrypt_v2(paste: dict, key_b58: str, password: str = "") -> str:
    """PrivateBin format v2 (AES-256-GCM, PBKDF2-SHA256, optional zlib)."""
    from Cryptodome.Cipher import AES
    from Cryptodome.Hash import SHA256
    from Cryptodome.Protocol.KDF import PBKDF2

    adata = paste.get("adata")
    if not isinstance(adata, list) or not adata or not isinstance(adata[0], list):
        raise PluginError("this PrivateBin paste uses an old format OpenGrab cannot read")
    iv_b64, salt_b64, iterations, key_bits, tag_bits, algo, mode, compression = adata[0][:8]
    if (algo, mode) != ("aes", "gcm"):
        raise PluginError(f"unsupported PrivateBin cipher {algo}-{mode}")
    key = b58decode(key_b58)
    key = key.rjust(32, b"\0")[-32:]
    material = key + password.encode("utf-8")
    derived = PBKDF2(material, base64.b64decode(salt_b64), dkLen=int(key_bits) // 8,
                     count=int(iterations), hmac_hash_module=SHA256)
    blob = base64.b64decode(paste.get("ct", ""))
    tag_len = int(tag_bits) // 8
    cipher = AES.new(derived, AES.MODE_GCM, nonce=base64.b64decode(iv_b64), mac_len=tag_len)
    # the authenticated data is the adata array exactly as the page serialised it
    cipher.update(json.dumps(adata, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    try:
        plain = cipher.decrypt_and_verify(blob[:-tag_len], blob[-tag_len:])
    except ValueError:
        if password:
            raise LoginRequired(hint="the PrivateBin password saved for this site is wrong")
        raise LoginRequired(hint="this PrivateBin paste needs a password: add it as an account for this site")
    if compression == "zlib":
        plain = zlib.decompress(plain, -15)
    body = json.loads(plain.decode("utf-8"))
    return str(body.get("paste", ""))


class PrivateBinPlugin(SitePlugin):
    id = "privatebin"
    name = "PrivateBin pastes"
    site = ""
    origin = "builtin"
    supports_captcha = False
    supports_login = True
    description = "PrivateBin pastes (encrypted link lists): decrypts with the key in the link, one child per link."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url, ctx):
        raise NotImplementedError  # resolve fetches and decrypts once

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a PrivateBin link")
        base, paste_id, key = parsed
        r = ctx.client.get(f"{base}?pasteid={paste_id}",
                           headers={"x-requested-with": "JSONHttpRequest", "accept": "application/json"},
                           detect_challenges=False)
        try:
            data = r.json()
        except ValueError:
            raise PluginError(f"the paste site did not answer like PrivateBin (HTTP {r.status_code})")
        if data.get("status", 0) != 0:
            raise PluginError(f"PrivateBin: {data.get('message') or 'paste not found or expired'}")
        password = ""
        try:
            acc = ctx.account_for(site=urlparse(base).hostname or "")
            password = (getattr(acc, "password", "") or "") if acc else ""
        except Exception:
            password = ""
        text = decrypt_v2(data, key, password)
        links = extract_links(text, base)
        if not links:
            raise PluginError("the PrivateBin paste has no download links in it")
        return ResolveResult(url="", filename=f"paste {paste_id}", children=[[u, ""] for u in links])
