"""Hash verify (M11e): checksum parsing, chunked file hashing, verification and bad-chunk detection."""
from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass

ALGOS = ("crc32", "md5", "sha1", "sha256", "sha512")
_BY_LENGTH = {8: "crc32", 32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
_HEX = set("0123456789abcdef")


class _Crc32:
    def __init__(self) -> None:
        self.value = 0

    def update(self, data: bytes) -> None:
        self.value = zlib.crc32(data, self.value)

    def hexdigest(self) -> str:
        return f"{self.value & 0xFFFFFFFF:08x}"


def _norm_algo(algo: str) -> str:
    name = (algo or "").strip().lower().replace("-", "")
    if name not in ALGOS:
        raise ValueError(f"unsupported algorithm: {name}")
    return name


def new_hasher(algo: str):
    name = _norm_algo(algo)
    if name == "crc32":
        return _Crc32()
    return hashlib.new(name)


def _clean_hex(value: str) -> str:
    digest = (value or "").strip().lower()
    if not digest or any(c not in _HEX for c in digest):
        raise ValueError("bad digest")
    return digest


def parse_expected(text: str) -> tuple[str, str]:
    """'sha256:<hex>' or a bare hex digest (algorithm inferred from its length) -> (algo, hex)."""
    raw = (text or "").strip()
    if ":" in raw:
        algo, _, digest = raw.partition(":")
        algo = _norm_algo(algo)
        digest = _clean_hex(digest)
        if _BY_LENGTH.get(len(digest)) != algo:
            raise ValueError("digest length does not match algorithm")
        return algo, digest
    digest = _clean_hex(raw)
    algo = _BY_LENGTH.get(len(digest))
    if algo is None:
        raise ValueError("unknown digest length")
    return algo, digest


def hash_bytes(data: bytes, algo: str) -> str:
    h = new_hasher(algo)
    h.update(data)
    return h.hexdigest()


def hash_file(path, algo: str, chunk_size: int = 1 << 20) -> str:
    if chunk_size <= 0:
        raise ValueError("bad chunk_size")
    h = new_hasher(algo)
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    algo: str
    expected: str
    actual: str
    error: str = ""


def verify_file(path, expected: str, chunk_size: int = 1 << 20) -> VerifyResult:
    algo, digest = parse_expected(expected)
    try:
        actual = hash_file(path, algo, chunk_size)
    except OSError as exc:
        return VerifyResult(False, algo, digest, "", f"read failed: {exc.__class__.__name__}")
    if actual == digest:
        return VerifyResult(True, algo, digest, actual)
    return VerifyResult(False, algo, digest, actual, "mismatch")


def parse_checksum_file(text: str) -> dict[str, tuple[str, str]]:
    """Parse sha256sum/md5sum lines ('<hex>  name' or '<hex> *name'); skip blanks, comments and bad lines."""
    out: dict[str, tuple[str, str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        name = name.strip()
        if name.startswith("*"):
            name = name[1:]
        try:
            parsed = parse_expected(digest)
        except ValueError:
            continue
        if name:
            out[name] = parsed
    return out


def bad_chunks(path, chunk_size: int, expected: list[str], algo: str = "sha256") -> list[int]:
    """Indices of fixed-size chunks whose hash differs from expected (missing data counts as bad)."""
    if chunk_size <= 0:
        raise ValueError("bad chunk_size")
    bad: list[int] = []
    with open(path, "rb") as f:
        for index, want in enumerate(expected):
            block = f.read(chunk_size)
            if not block or hash_bytes(block, algo) != (want or "").strip().lower():
                bad.append(index)
    return bad