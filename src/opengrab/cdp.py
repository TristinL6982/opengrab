"""
A minimal standard-library CDP client used by the Edge browser solver.
No headless, no visible window, no Playwright.
"""

import json
import os
import struct
import time
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

    def call(self, method: str, params: dict = None, timeout: float = 10.0) -> dict:
        msg_id = self._next_id
        self._next_id += 1
        self.transport.send_text(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
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