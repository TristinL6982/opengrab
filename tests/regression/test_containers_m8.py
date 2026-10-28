import pytest

from opengrab.containers import ContainerError, decode_rsdf, detect_container, encode_rsdf


def test_detect_container():
    assert detect_container("a.DLC") == "dlc"
    assert detect_container("x.rsdf") == "rsdf"
    assert detect_container("y.ccf") == "ccf"
    assert detect_container("z.txt") is None
    assert detect_container("noext") is None
    assert detect_container("dlc") is None


def test_decode_rsdf_dedupes_in_order():
    encoded = encode_rsdf(["https://a.com/1", "https://a.com/2", "https://a.com/1"])
    assert decode_rsdf(encoded) == ["https://a.com/1", "https://a.com/2"]


def test_decode_rsdf_whitespace():
    encoded = encode_rsdf(["https://a.com/1", "https://a.com/2"])
    chunks = [encoded[i:i + 20] for i in range(0, len(encoded), 20)]
    formatted = b" \r\n\t".join(chunks)
    assert decode_rsdf(formatted) == ["https://a.com/1", "https://a.com/2"]


def test_decode_rsdf_ccf_prefix():
    assert decode_rsdf(encode_rsdf(["CCF: https://b.com/x"])) == ["https://b.com/x"]


def test_decode_rsdf_bad_hex():
    with pytest.raises(ContainerError):
        decode_rsdf(b"zz")


def test_decode_rsdf_non_ascii():
    with pytest.raises(ContainerError):
        decode_rsdf("\u00e9\u00e9".encode("utf-8"))


def test_decode_rsdf_empty():
    with pytest.raises(ContainerError):
        decode_rsdf(b"")
    with pytest.raises(ContainerError):
        decode_rsdf(b"  \n ")


def test_decode_rsdf_bad_base64():
    with pytest.raises(ContainerError):
        decode_rsdf(b"!!!notbase64".hex().encode("ascii"))