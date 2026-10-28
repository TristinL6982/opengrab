import io
import json
import urllib
import urllib.error
from opengrab.cdp import (
    CdpError,
    discover_ws_url,
    encode_frame,
    decode_frame,
    CdpClient
)
import pytest


class FakeTransport:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []
        self.recv_calls = 0
        self.closed = False

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def recv_text(self, timeout):
        self.recv_calls += 1
        if not self.incoming:
            raise CdpError("closed")
        return json.dumps(self.incoming.pop(0))

    def close(self):
        self.closed = True


def fake(url, timeout):
    fake.calls.append((url, timeout))
    return io.BytesIO(b'{"Browser": "Edg/130.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}')


def test_discover_ws_url():
    fake.calls = []
    result = discover_ws_url(9222, opener=fake)
    assert result == "ws://127.0.0.1:9222/devtools/browser/abc"
    assert fake.calls == [("http://127.0.0.1:9222/json/version", 5.0)]


def test_discover_ws_url_errors():
    def raise_url_error(url, timeout):
        raise urllib.error.URLError("refused")
    with pytest.raises(CdpError, match="devtools not reachable"):
        discover_ws_url(9222, opener=raise_url_error)
    def empty_json(url, timeout):
        return io.BytesIO(b"{}")
    with pytest.raises(CdpError, match="no webSocketDebuggerUrl"):
        discover_ws_url(9222, opener=empty_json)
    def bad_json(url, timeout):
        return io.BytesIO(b"not json")
    with pytest.raises(CdpError, match="devtools not reachable"):
        discover_ws_url(9222, opener=bad_json)


def test_encode_small():
    result = encode_frame(b"hi", mask_key=b"\x01\x02\x03\x04")
    expected = bytes([0x81, 0x82, 1, 2, 3, 4, ord("h") ^ 1, ord("i") ^ 2])
    assert result == expected
    result = encode_frame(b"", opcode=8, mask_key=b"\x00\x00\x00\x00")
    expected = bytes([0x88, 0x80, 0, 0, 0, 0])
    assert result == expected


def test_encode_lengths():
    f = encode_frame(b"a" * 200, mask_key=b"\x00\x00\x00\x00")
    assert f[1] == 0x80 | 126
    assert f[2:4] == (200).to_bytes(2, "big")
    assert len(f) == 4 + 4 + 200

    g = encode_frame(b"a" * 70000, mask_key=b"\x00\x00\x00\x00")
    assert g[1] == 0x80 | 127
    assert g[2:10] == (70000).to_bytes(8, "big")
    assert len(g) == 10 + 4 + 70000


def test_decode_frame():
    result = decode_frame(bytes([0x81, 5]) + b"hello" + b"extra")
    assert result == (True, 1, b"hello", 7)

    # Test roundtrip of encoded frame
    original = b"abc"
    frame = encode_frame(original, mask_key=b"\x09\x08\x07\x06")
    result = decode_frame(frame)
    assert result == (True, 1, original, len(frame))

    # Server frame with 16-bit length
    payload = b"z" * 300
    frame = bytes([0x81, 126]) + (300).to_bytes(2, "big") + payload
    result = decode_frame(frame)
    assert result[2] == payload
    assert result[3] == 304

    # Test unmasked frame
    unmasked = bytes([0x01, 3]) + b"abc"
    result = decode_frame(unmasked)
    assert result == (False, 1, b"abc", 5)  # 2-byte header + 3-byte payload = 5 bytes total


def test_decode_incomplete():
    assert decode_frame(b"") is None
    assert decode_frame(b"\x81") is None
    assert decode_frame(bytes([0x81, 5]) + b"he") is None
    assert decode_frame(bytes([0x81, 126, 1])) is None


def test_call_result_and_events():
    t = FakeTransport([
        {"method": "Page.loadEventFired", "params": {"timestamp": 1}},
        {"id": 99, "result": {}},
        {"id": 1, "result": {"frameId": "F"}}
    ])
    c = CdpClient(t)
    result = c.call("Page.navigate", {"url": "https://x.test/"})
    assert result == {"frameId": "F"}
    assert t.sent == [{"id": 1, "method": "Page.navigate", "params": {"url": "https://x.test/"}}]
    assert c.events == [{"method": "Page.loadEventFired", "params": {"timestamp": 1}}]

    # Send another message to make sure it's in the queue before calling Page.enable
    t.incoming.append({"id": 2, "result": {}})
    result = c.call("Page.enable")
    assert result == {}
    assert t.sent[1] == {"id": 2, "method": "Page.enable", "params": {}}


def test_call_error():
    t = FakeTransport([{"id": 1, "error": {"code": -32000, "message": "Not allowed"}}])
    c = CdpClient(t)
    with pytest.raises(CdpError, match="Page.navigate: Not allowed"):
        c.call("Page.navigate", {"url": "https://x.test/"})


def test_call_timeout():
    times = iter([0.0, 1.0, 11.0])
    t = FakeTransport([{"method": "Network.dataReceived", "params": {}}])
    c = CdpClient(t, clock=lambda: next(times))
    with pytest.raises(CdpError, match="timeout waiting for Page.navigate"):
        c.call("Page.navigate", timeout=10.0)
    assert c.events == [{"method": "Network.dataReceived", "params": {}}]


def test_wait_event():
    t = FakeTransport([
        {"method": "Network.x", "params": {"a": 1}},
        {"method": "Page.loadEventFired", "params": {"timestamp": 2}}
    ])
    c = CdpClient(t)
    result = c.wait_event("Page.loadEventFired")
    assert result == {"timestamp": 2}
    assert c.events == [{"method": "Network.x", "params": {"a": 1}}]
    n = t.recv_calls
    result = c.wait_event("Network.x")
    assert result == {"a": 1}
    assert t.recv_calls == n  # Should not have called recv_text again
    assert c.events == []


def test_wait_event_timeout_and_close():
    times = iter([0.0, 6.0])
    t = FakeTransport([])
    c = CdpClient(t, clock=lambda: next(times))
    with pytest.raises(CdpError, match="timeout waiting for event Page.loadEventFired"):
        c.wait_event("Page.loadEventFired", timeout=5.0)
    c.close()
    assert c.transport.closed is True