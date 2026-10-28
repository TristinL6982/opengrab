import re
import base64
from Cryptodome.Cipher import AES

class CnlError(ValueError):
    pass

def key_from_jk(jk: str) -> bytes:
    # Find the first quoted string in JavaScript code
    match = re.search(r'["\']([^"\']*)["\']', jk)
    if not match:
        raise CnlError("No quoted string found")
    
    hex_string = match.group(1)
    # Check if it's valid hex
    try:
        key = bytes.fromhex(hex_string)
    except ValueError:
        raise CnlError("Invalid hex string")
    
    # Check key length (must be 16, 24, or 32 bytes)
    if len(key) not in (16, 24, 32):
        raise CnlError(f"Key must be 16, 24, or 32 bytes long, got {len(key)}")
    
    return key

def decrypt_crypted(crypted: str, key: bytes) -> str:
    # Remove all whitespace (CNL payloads may contain line breaks), then strict base64
    data = re.sub(r"\s+", "", crypted)
    try:
        encrypted_data = base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise CnlError("Invalid base64 data")
    if not encrypted_data or len(encrypted_data) % 16:
        raise CnlError("Crypted data length must be a non-zero multiple of 16")
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv=key)
    except ValueError as e:
        raise CnlError(f"Invalid AES key: {e}")
    decrypted = cipher.decrypt(encrypted_data)
    # Decode as utf-8, strip trailing NUL padding and whitespace
    return decrypted.decode("utf-8", errors="replace").rstrip("\x00 \t\n\r")


def parse_links(text: str) -> list[str]:
    # Split on \r\n, \n and \r, strip each line, keep non-empty lines, drop duplicates keeping first order
    lines = re.split(r'\r\n|\n|\r', text)
    stripped_lines = [line.strip() for line in lines]
    unique_lines = []
    seen = set()
    for line in stripped_lines:
        if line and line not in seen:
            unique_lines.append(line)
            seen.add(line)
    return unique_lines

def decode_addcrypted2(form: dict) -> dict:
    # Check for required fields
    if "jk" not in form or "crypted" not in form:
        raise CnlError("Missing required fields jk or crypted")
    
    # Extract and validate key
    key = key_from_jk(form["jk"])
    
    # Decrypt the crypted data
    decrypted_text = decrypt_crypted(form["crypted"], key)
    
    # Parse links
    links = parse_links(decrypted_text)
    
    # Process passwords (non-empty stripped lines of form.get("passwords",""))
    passwords = [line.strip() for line in form.get("passwords", "").splitlines() if line.strip()]
    
    # Return the result with source and package from form or empty string
    return {
        "links": links,
        "passwords": passwords,
        "source": form.get("source", ""),
        "package": form.get("package", "")
    }