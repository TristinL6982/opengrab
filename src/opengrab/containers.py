from Cryptodome.Cipher import AES
import base64
import re

RSDF_KEY = bytes.fromhex("8C35192D964DC3182C6F84F3252239EB4A320D2500000000")
RSDF_IV = b"\xff" * 16


class ContainerError(ValueError):
    pass


def detect_container(filename: str) -> str | None:
    ext = filename.lower().split(".")[-1]
    if ext in ("dlc", "rsdf", "ccf"):
        return ext
    return None


def decode_rsdf(data: bytes) -> list[str]:
    # Remove all ASCII whitespace
    no_whitespace = re.sub(r"\s+", "", data.decode("ascii"))
    try:
        hex_bytes = bytes.fromhex(no_whitespace)
    except Exception:
        raise ContainerError()
    
    if len(hex_bytes) == 0:
        raise ContainerError()
    
    cipher = AES.new(RSDF_KEY, AES.MODE_CFB, iv=RSDF_IV, segment_size=8)
    
    lines = hex_bytes.split(b"\n")
    results = []
    seen = set()
    
    for line in lines:
        line = line.strip(b"\r")
        if not line:
            continue
            
        try:
            decoded = base64.b64decode(line)
        except Exception:
            raise ContainerError()
            
        decrypted = cipher.decrypt(decoded)
        try:
            link = decrypted.decode("utf-8", errors="replace").strip()
        except Exception:
            raise ContainerError()
            
        if link.startswith("CCF: "):
            link = link[5:].strip()
            
        if link and link not in seen:
            results.append(link)
            seen.add(link)
    
    return results


def encode_rsdf(links: list[str]) -> bytes:
    cipher = AES.new(RSDF_KEY, AES.MODE_CFB, iv=RSDF_IV, segment_size=8)
    encoded_lines = []
    for link in links:
        encrypted = cipher.encrypt(link.encode("utf-8"))
        encoded_line = base64.b64encode(encrypted).decode("ascii")
        encoded_lines.append(encoded_line)
    
    return "\n".join(encoded_lines).encode("ascii").hex().upper().encode("ascii")