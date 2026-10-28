from opengrab.containers import detect_container, decode_rsdf, encode_rsdf, ContainerError


def test_detect_container():
    assert detect_container("a.DLC") == "dlc"
    assert detect_container("x.rsdf") == "rsdf"
    assert detect_container("y.ccf") == "ccf"
    assert detect_container("z.txt") is None
    assert detect_container("noext") is None


def test_decode_rsdf():
    links = ["https://a.com/1", "https://a.com/2", "https://a.com/1"]
    encoded = encode_rsdf(links)
    decoded = decode_rsdf(encoded)
    assert decoded == ["https://a.com/1", "https://a.com/2"]


def test_decode_rsdf_whitespace():
    links = ["https://a.com/1", "https://a.com/2"]
    encoded = encode_rsdf(links)
    # Insert whitespace and line breaks every 20 characters (like in the original test)
    formatted = b"\n".join(encoded[i:i+20] for i in range(0, len(encoded), 20))
    decoded = decode_rsdf(formatted)
    assert decoded == ["https://a.com/1", "https://a.com/2"]


def test_decode_rsdf_ccf_prefix():
    links = ["CCF: https://b.com/x"]
    encoded = encode_rsdf(links)
    decoded = decode_rsdf(encoded)
    assert decoded == ["https://b.com/x"]


def test_decode_rsdf_errors():
    # Invalid hex
    try:
        decode_rsdf(b"zz")
        assert False, "Should raise ContainerError"
    except ContainerError:
        pass
        
    # Empty data
    try:
        decode_rsdf(b"")
        assert False, "Should raise ContainerError"
    except ContainerError:
        pass
        
    # Invalid base64 string (hex of "!!!notbase64")
    try:
        decode_rsdf(b"2121216e6f74626173653634")
        assert False, "Should raise ContainerError"
    except ContainerError:
        pass