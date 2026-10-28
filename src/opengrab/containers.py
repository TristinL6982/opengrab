"""Container files (DLC, RSDF, CCF): type detection and RSDF decoding."""
import base64
import binascii
import os
import re

from Cryptodome.Cipher import AES

RSDF_KEY = bytes.fromhex("8C35192D964DC3182C6F84F3252239EB4A320D2500000000")
RSDF_IV = b"\xff" * 16

_CONTAINER_TYPES = ("dlc", "rsdf", "ccf")


class ContainerError(ValueError):
    pass


def detect_container(filename: str) -> str | None:
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    return ext if ext in _CONTAINER_TYPES else None


def _new_cipher():
    return AES.new(RSDF_KEY, AES.MODE_CFB, iv=RSDF_IV, segment_size=8)


def decode_rsdf(data: bytes) -> list[str]:
    try:
        raw = bytes.fromhex(re.sub(rb"\s+", b"", data).decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ContainerError("RSDF data is not valid hex") from exc
    if not raw:
        raise ContainerError("RSDF data is empty")

    cipher = _new_cipher()
    results: list[str] = []
    seen: set[str] = set()
    for line in raw.split(b"\n"):
        line = line.strip(b"\r")
        if not line:
            continue
        try:
            encrypted = base64.b64decode(line, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ContainerError("RSDF line is not valid base64") from exc
        link = cipher.decrypt(encrypted).decode("utf-8", errors="replace").strip()
        if link.startswith("CCF: "):
            link = link[len("CCF: "):].strip()
        if link and link not in seen:
            seen.add(link)
            results.append(link)
    return results


def encode_rsdf(links: list[str]) -> bytes:
    cipher = _new_cipher()
    lines = [base64.b64encode(cipher.encrypt(link.encode("utf-8"))) for link in links]
    return b"\n".join(lines).hex().upper().encode("ascii")