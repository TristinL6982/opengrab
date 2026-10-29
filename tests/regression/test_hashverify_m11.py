import hashlib

import pytest

from opengrab.hashverify import (
    ALGOS,
    VerifyResult,
    bad_chunks,
    hash_bytes,
    hash_file,
    new_hasher,
    parse_checksum_file,
    parse_expected,
    verify_file,
)

SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
MD5_ABC = "900150983cd24fb0d6963f7d28e17f72"


def test_hash_bytes_known_vectors():
    assert hash_bytes(b"abc", "sha256") == SHA256_ABC
    assert hash_bytes(b"abc", "MD5") == MD5_ABC
    assert hash_bytes(b"abc", "crc32") == "352441c2"
    assert hash_bytes(b"abc", "sha-1") == hashlib.sha1(b"abc").hexdigest()
    assert hash_bytes(b"abc", "sha512") == hashlib.sha512(b"abc").hexdigest()


def test_new_hasher_rejects_unknown():
    assert ALGOS == ("crc32", "md5", "sha1", "sha256", "sha512")
    with pytest.raises(ValueError):
        new_hasher("whirlpool")


def test_parse_expected_prefixed_and_bare():
    assert parse_expected("sha256:" + SHA256_ABC.upper()) == ("sha256", SHA256_ABC)
    assert parse_expected("  " + MD5_ABC + "\n") == ("md5", MD5_ABC)
    assert parse_expected("CRC32:352441C2") == ("crc32", "352441c2")
    assert parse_expected(hashlib.sha1(b"").hexdigest())[0] == "sha1"


@pytest.mark.parametrize("bad", ["", "xyz", "sha256:" + MD5_ABC, "md4:" + MD5_ABC, "abc123", "g" * 64])
def test_parse_expected_rejects(bad):
    with pytest.raises(ValueError):
        parse_expected(bad)


def test_hash_file_chunked_matches_hashlib(tmp_path):
    data = bytes(range(256)) * 50
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    want = hashlib.sha256(data).hexdigest()
    assert hash_file(p, "sha256") == want
    assert hash_file(p, "sha256", chunk_size=7) == want
    with pytest.raises(ValueError):
        hash_file(p, "sha256", chunk_size=0)


def test_verify_file(tmp_path):
    p = tmp_path / "abc.txt"
    p.write_bytes(b"abc")
    assert verify_file(p, SHA256_ABC) == VerifyResult(True, "sha256", SHA256_ABC, SHA256_ABC)
    wrong = "0" * 32
    assert verify_file(p, "md5:" + wrong) == VerifyResult(False, "md5", wrong, MD5_ABC, "mismatch")
    missing = verify_file(tmp_path / "nope.bin", MD5_ABC)
    assert missing == VerifyResult(False, "md5", MD5_ABC, "", "read failed: FileNotFoundError")


def test_parse_checksum_file():
    text = ("# comment\n\n" + SHA256_ABC + "  file one.iso\n" + MD5_ABC + " *b.zip\n"
            + "not a line\n" + "zz" * 16 + "  c.bin\n")
    assert parse_checksum_file(text) == {"file one.iso": ("sha256", SHA256_ABC), "b.zip": ("md5", MD5_ABC)}


def test_bad_chunks(tmp_path):
    p = tmp_path / "d.bin"
    p.write_bytes(b"A" * 10 + b"B" * 10 + b"C" * 5)
    good = [hash_bytes(b"A" * 10, "sha256"), hash_bytes(b"B" * 10, "sha256"), hash_bytes(b"C" * 5, "sha256")]
    assert bad_chunks(p, 10, good) == []
    broken = [good[0], hash_bytes(b"X" * 10, "sha256"), good[2].upper()]
    assert bad_chunks(p, 10, broken) == [1]
    assert bad_chunks(p, 10, good + [good[0]]) == [3]
    with pytest.raises(ValueError):
        bad_chunks(p, 0, good)