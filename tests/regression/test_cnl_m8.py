import base64
from Cryptodome.Cipher import AES
import pytest

from opengrab.cnl import CnlError, key_from_jk, decrypt_crypted, parse_links, decode_addcrypted2


def test_key_from_jk():
    # Test valid hex string
    result = key_from_jk("function f(){ return '31323334353637383930313233343536';}")
    expected = b"1234567890123456"
    assert result == expected
    
    # Test with double quotes
    result = key_from_jk('function f(){ return "31323334353637383930313233343536";}')
    expected = b"1234567890123456"
    assert result == expected
    
    # Test invalid hex
    with pytest.raises(CnlError):
        key_from_jk("function f(){ return 'zz';}")
    
    # Test no quoted string
    with pytest.raises(CnlError):
        key_from_jk("function f() { return 123; }")
    
    # Test wrong length (5 bytes)
    with pytest.raises(CnlError):
        key_from_jk("function f(){ return '3132333435';}")


def test_decrypt_crypted():
    # Create test data with AES-CBC encryption (key = iv = b"1234567890123456")
    key = b"1234567890123456"
    
    # Test valid decryption
    plaintext = "https://a.com/1\r\nhttps://a.com/2\n"
    # Pad plaintext to multiple of 16 bytes
    padded_plaintext = plaintext.encode('utf-8') + b"\x00" * (16 - len(plaintext) % 16)
    
    cipher = AES.new(key, AES.MODE_CBC, iv=key)
    encrypted_data = cipher.encrypt(padded_plaintext)
    encoded_crypted = base64.b64encode(encrypted_data).decode('utf-8')
    
    result = decrypt_crypted(encoded_crypted, key)
    assert result == "https://a.com/1\r\nhttps://a.com/2"

    # Whitespace and line breaks inside the base64 are ignored
    wrapped = encoded_crypted[:10] + "\n " + encoded_crypted[10:]
    assert decrypt_crypted(wrapped, key) == "https://a.com/1\r\nhttps://a.com/2"
    
    # Test invalid base64
    with pytest.raises(CnlError):
        decrypt_crypted("not base64!!", key)
        
    # Test wrong length (10 bytes not multiple of 16)
    with pytest.raises(CnlError):
        decrypt_crypted(base64.b64encode(b"1234567890").decode('utf-8'), key)


def test_parse_links():
    result = parse_links("a\r\n\n b \na\rc")
    expected = ["a", "b", "c"]
    assert result == expected


def test_decode_addcrypted2():
    # Test with full form data
    key = b"1234567890123456"
    
    # Create encrypted data for testing
    plaintext = "https://a.com/1\r\nhttps://a.com/2\r\nhttps://a.com/1"  # Duplicate line
    padded_plaintext = plaintext.encode('utf-8') + b"\x00" * (16 - len(plaintext) % 16)
    
    cipher = AES.new(key, AES.MODE_CBC, iv=key)
    encrypted_data = cipher.encrypt(padded_plaintext)
    encoded_crypted = base64.b64encode(encrypted_data).decode('utf-8')
    
    form = {
        "jk": "function f(){ return '31323334353637383930313233343536';}",
        "crypted": encoded_crypted,
        "passwords": "pw1\n\npw2",
        "source": "https://s.com",
        "package": "Pkg"
    }
    
    result = decode_addcrypted2(form)
    expected = {
        "links": ["https://a.com/1", "https://a.com/2"],
        "passwords": ["pw1", "pw2"],
        "source": "https://s.com",
        "package": "Pkg"
    }
    assert result == expected
    
    # Test with only required fields
    form = {
        "jk": "function f(){ return '31323334353637383930313233343536';}",
        "crypted": encoded_crypted
    }
    
    result = decode_addcrypted2(form)
    expected = {
        "links": ["https://a.com/1", "https://a.com/2"],
        "passwords": [],
        "source": "",
        "package": ""
    }
    assert result == expected
    
    # Test missing required fields
    with pytest.raises(CnlError):
        decode_addcrypted2({"jk": "..."})

def test_decrypt_crypted_rejects_empty_and_bad_key():
    with pytest.raises(CnlError):
        decrypt_crypted("", b"1234567890123456")
    with pytest.raises(CnlError):
        decrypt_crypted(base64.b64encode(b"\x00" * 16).decode("ascii"), b"short")
