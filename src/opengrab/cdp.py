"""
A minimal standard-library CDP client used by the Edge browser solver.
No headless, no visible window, no Playwright.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request
import urllib.error


class CdpError(Exception):
    pass


def discover_ws_url(port: int, host: str = "127.0.0.1", timeout: float = 5.0, opener=None) -> str:
    url = "http://%s:%d/json/version" % (host, port)
    opener = opener or urllib.request.urlopen
    try:
        with opener(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise CdpError("devtools not reachable: " + str(e))
    ws = data.get("webSocketDebuggerUrl", "") if isinstance(data, dict) else ""
    if not ws:
        raise CdpError("no webSocketDebuggerUrl")
    return ws


def encode_frame(payload: bytes, opcode: int = 1, mask_key: bytes = None) -> bytes:
    mask_key = mask_key or os.urandom(4)
    first_byte = 0x80 | opcode
    n = len(payload)
    if n < 126:
        second_byte = 0x80 | n
        frame = bytes([first_byte, second_byte])
    elif n <= 0xFFFF:
        second_byte = 0x80 | 126
        frame = bytes([first_byte, second_byte]) + struct.pack(">H", n)
    else:
        second_byte = 0x80 | 127
        frame = bytes([first_byte, second_byte]) + struct.pack(">Q", n)
    frame += mask_key
    masked_payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return frame + masked_payload


def decode_frame(buf: bytes):
    if len(buf) < 2:
        return None
    fin = bool(buf[0] & 0x80)
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    n = buf[1] & 0x7F
    pos = 2
    if n == 126:
        if len(buf) < 4:
            return None
        n = struct.unpack(">H", buf[2:4])[0]
        pos = 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n = struct.unpack(">Q", buf[2:10])[0]
        pos = 10

    if masked:
        if len(buf) < pos + 4:
            return None
        mask = buf[pos:pos + 4]
        pos += 4
    if len(buf) < pos + n:
        return None
    payload = bytes(buf[pos:pos + n])
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return (fin, opcode, payload, pos + n)


class CdpClient:
    def __init__(self, transport, clock=time.monotonic):
        self.transport = transport
        self.clock = clock
        self._next_id = 1
        self.events = []

    def call(self, method: str, params: dict = None, timeout: float = 10.0,
             session_id: str = "") -> dict:
        """Send one command; ``session_id`` targets a page attached with
        Target.attachToTarget(flatten=True)."""
        msg_id = self._next_id
        self._next_id += 1
        msg = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.transport.send_text(json.dumps(msg))
        deadline = self.clock() + timeout
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise CdpError("timeout waiting for " + method)
            msg = json.loads(self.transport.recv_text(remaining))
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise CdpError(method + ": " + str(msg["error"].get("message", "")))
                return msg.get("result", {})
            elif "id" not in msg and "method" in msg:
                self.events.append(msg)
            # Any other message (a different id) is ignored.

    def wait_event(self, name: str, timeout: float = 10.0) -> dict:
        for i, event in enumerate(self.events):
            if event.get("method") == name:
                self.events.pop(i)
                return event.get("params", {})
        deadline = self.clock() + timeout
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise CdpError("timeout waiting for event " + name)
            msg = json.loads(self.transport.recv_text(remaining))
            if msg.get("method") == name and "id" not in msg:
                return msg.get("params", {})
            elif "id" not in msg and "method" in msg:
                self.events.append(msg)

    def close(self):
        self.transport.close()


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")


class SocketTransport:
    def __init__(self, ws_url: str, timeout: float = 10.0, connect=None, key: str = None):
        parts = urllib.parse.urlsplit(ws_url)
        if parts.scheme != "ws":
            raise CdpError("only ws:// urls are supported")
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
            
        connect = connect or socket.create_connection
        try:
            self.sock = connect((host, port), timeout)
        except OSError as e:
            raise CdpError("cannot connect: " + str(e))
        
        self._buf = b""
        self._frag = b""
        key = key or base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)).encode("ascii"))
        
        # Read handshake
        while b"\r\n\r\n" not in self._buf:
            data = self.sock.recv(4096)
            if not data:
                raise CdpError("handshake failed: connection closed")
            self._buf += data
        
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise CdpError("handshake failed: " + lines[0])
        
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.lower()] = value.strip()
        
        if headers.get("sec-websocket-accept", "") != ws_accept(key):
            raise CdpError("bad Sec-WebSocket-Accept")
    
    def send_text(self, text: str):
        self.sock.sendall(encode_frame(text.encode("utf-8"), 1))
    
    def recv_text(self, timeout: float) -> str:
        self.sock.settimeout(timeout)
        while True:
            f = decode_frame(self._buf)
            if f is None:
                try:
                    data = self.sock.recv(65536)
                except OSError as e:
                    raise CdpError("socket error: " + str(e))
                if not data:
                    raise CdpError("connection closed")
                self._buf += data
                continue
            
            fin, opcode, payload, used = f
            self._buf = self._buf[used:]
            
            if opcode in (0, 1):
                self._frag += payload
                if fin:
                    text = self._frag.decode("utf-8")
                    self._frag = b""
                    return text
            elif opcode == 8:
                raise CdpError("closed by browser")
            elif opcode == 9:
                self.sock.sendall(encode_frame(payload, 10))
            # Any other opcode (10 pong, 2 binary) is ignored
    
    def close(self):
        try:
            self.sock.sendall(encode_frame(b"", 8))
        except OSError:
            pass
        self.sock.close()


EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
]


def find_browser(candidates=None, exists=os.path.exists) -> str:
    paths = candidates if candidates is not None else EDGE_PATHS
    for path in paths:
        if exists(path):
            return path
    return ""


def browser_args(exe: str, port: int, user_data_dir: str, url: str = "about:blank") -> list:
    return [exe, "--remote-debugging-port=%d" % port, "--user-data-dir=" + user_data_dir, "--no-first-run", "--no-default-browser-check", url]