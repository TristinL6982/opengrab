import json
import os
import pytest
import socket
import struct
import urllib.parse
from opengrab.cdp import (
    CdpError,
    encode_frame,
    decode_frame,
    CdpClient,
    ws_accept,
    SocketTransport,
    find_browser,
    browser_args,
    EDGE_PATHS,
)

KEY = "dGhlIHNhbXBsZSBub25jZQ=="
OK = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n"


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)  # a list of bytes, or Exception instances to raise
        self.sent = []
        self.timeouts = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(bytes(data))

    def recv(self, n):
        if not self.chunks:
            return b""
        c = self.chunks.pop(0)
        if isinstance(c, Exception):
            raise c
        return c

    def settimeout(self, t):
        self.timeouts.append(t)

    def close(self):
        self.closed = True


CALLS = []


def make(chunks, url="ws://127.0.0.1:9222/devtools/browser/abc"):
    del CALLS[:]
    sock = FakeSocket(chunks)

    def fake_connect(addr, timeout):
        CALLS.append((addr, timeout))
        return sock

    t = SocketTransport(url, connect=fake_connect, key=KEY)
    return t, sock


def server_frame(payload, opcode=1, fin=True):
    return bytes([(0x80 if fin else 0) | opcode, len(payload)]) + payload


def test_ws_accept():
    assert ws_accept(KEY) == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_handshake_request():
    t, s = make([OK])
    assert CALLS == [(("127.0.0.1", 9222), 10.0)]
    assert s.sent[0] == b"GET /devtools/browser/abc HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"


def test_handshake_split_and_leftover():
    t, s = make([OK[:20], OK[20:] + server_frame(b'{"id": 1}')])
    assert t.recv_text(5.0) == '{"id": 1}'
    assert s.timeouts == [5.0]


def test_handshake_errors():
    with pytest.raises(CdpError, match="only ws:// urls are supported"):
        SocketTransport("wss://x/y", connect=lambda a, t: FakeSocket([]), key=KEY)
    
    with pytest.raises(CdpError, match="handshake failed: HTTP/1.1 404 Not Found"):
        make([b"HTTP/1.1 404 Not Found\r\n\r\n"])
    
    with pytest.raises(CdpError, match="bad Sec-WebSocket-Accept"):
        make([OK.replace(b"s3pP", b"AAAA")])
    
    with pytest.raises(CdpError, match="handshake failed: connection closed"):
        make([])
    
    def fake_connect_raises(*args, **kwargs):
        raise OSError("refused")
    
    with pytest.raises(CdpError, match="cannot connect"):
        SocketTransport("ws://x/y", connect=fake_connect_raises, key=KEY)


def test_send_text_masked():
    t, s = make([OK])
    t.send_text("hi")
    f = s.sent[1]
    assert f[0] == 0x81
    assert f[1] == 0x82
    assert decode_frame(f) == (True, 1, b"hi", 8)


def test_recv_split_frame_and_fragments():
    t, s = make([OK, server_frame(b"hello")[:3], server_frame(b"hello")[3:], server_frame(b"ab", opcode=1, fin=False) + server_frame(b"cd", opcode=0)])
    assert t.recv_text(1.0) == "hello"
    assert t.recv_text(1.0) == "abcd"


def test_recv_ping_gets_pong():
    t, s = make([OK + server_frame(b"p1", opcode=9) + server_frame(b"x")])
    assert t.recv_text(1.0) == "x"
    assert decode_frame(s.sent[1]) == (True, 10, b"p1", 8)


def test_recv_close_and_errors():
    t, s = make([OK + server_frame(b"", opcode=8)])
    with pytest.raises(CdpError, match="closed by browser"):
        t.recv_text(1.0)

    t, s = make([OK])
    with pytest.raises(CdpError, match="connection closed"):
        t.recv_text(1.0)

    t, s = make([OK, socket.timeout("timed out")])
    with pytest.raises(CdpError, match="socket error"):
        t.recv_text(1.0)


def test_close_sends_close_frame():
    t, s = make([OK])
    t.close()
    assert decode_frame(s.sent[1])[1] == 8
    assert s.closed is True


def test_client_over_socket_transport():
    t, s = make([OK + server_frame(b'{"id": 1, "result": {"product": "Edg/130"}}')])
    result = CdpClient(t).call("Browser.getVersion")
    assert result == {"product": "Edg/130"}
    assert json.loads(decode_frame(s.sent[1])[2]) == {"id": 1, "method": "Browser.getVersion", "params": {}}


def test_find_browser_and_args():
    assert find_browser(["a.exe", "b.exe"], exists=lambda p: p == "b.exe") == "b.exe"
    assert find_browser(["a.exe"], exists=lambda p: False) == ""
    assert find_browser(exists=lambda p: "Edge" in p) == EDGE_PATHS[0]
    assert browser_args("e.exe", 9222, "D:\\prof") == ["e.exe", "--remote-debugging-port=9222", "--user-data-dir=D:\\prof", "--no-first-run", "--no-default-browser-check", "about:blank"]
    
    # Verify no headless
    args = browser_args("e.exe", 9222, "D:\\prof")
    assert "headless" not in " ".join(args)