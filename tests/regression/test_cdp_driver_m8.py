import json
import os
import pytest
import tempfile
from unittest.mock import patch

from opengrab.browser_solver import BrowserSolverUnavailable, CdpDriver


class FakeTransport:
    def __init__(self, results):
        self.results = results  # method name -> result dict
        self.sent = []
        self.closed = False

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def recv_text(self, timeout):
        m = self.sent[-1]
        return json.dumps({"id": m["id"], "result": self.results.get(m["method"], {})})

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def poll(self):
        return None


def make(results=None, discover=None, attempts=50):
    calls = {"popen": [], "factory": [], "sleep": []}
    
    t = FakeTransport(results or {})
    
    def popen(args):
        calls["popen"].append(args)
        proc = FakeProc()
        # Return the proc so it can be accessed 
        return proc

    def factory(ws):
        calls["factory"].append(ws)
        return t

    d = CdpDriver(
        port=9333,
        user_data_dir="D:\\prof",
        exe="e.exe",
        popen=popen,
        discover=discover or (lambda port: "ws://127.0.0.1:9333/devtools/browser/x"),
        transport_factory=factory,
        sleep=lambda s: calls["sleep"].append(s),
        attempts=attempts
    )
    
    # We are not returning proc because in tests we create it directly, 
    # but the function is structured so that we capture it in make().
    return d, t, None, calls


def test_not_found():
    with pytest.raises(BrowserSolverUnavailable, match="was not found"):
        CdpDriver(find=lambda: "", popen=lambda a: FakeProc())


def test_launch_args_and_attach():
    d, t, proc, calls = make()
    assert calls["popen"] == [ ["e.exe", "--remote-debugging-port=9333", "--user-data-dir=D:\\prof", "--no-first-run", "--no-default-browser-check", "about:blank"] ]
    assert calls["factory"] == ["ws://127.0.0.1:9333/devtools/browser/x"]
    assert calls["sleep"] == []


def test_discover_retries():
    call_count = 0
    
    def discover(port):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise Exception("down")
        return "ws://y"

    d, t, proc, calls = make(discover=discover)
    assert calls["sleep"] == [0.2, 0.2]
    assert calls["factory"] == ["ws://y"]


def test_discover_never():
    def discover(port):
        raise Exception("down")

    # This is a simplified version of what make() does
    calls = {"popen": [], "factory": [], "sleep": []}
    
    t = FakeTransport({})
    
    def popen(args):
        calls["popen"].append(args)
        proc = FakeProc()
        return proc

    def factory(ws):
        calls["factory"].append(ws)
        return t

    with pytest.raises(BrowserSolverUnavailable, match="devtools did not start: down"):
        d = CdpDriver(
            port=9333,
            user_data_dir="D:\\prof",
            exe="e.exe",
            popen=popen,
            discover=discover,
            transport_factory=factory,
            sleep=lambda s: calls["sleep"].append(s),
            attempts=3
        )
    
    assert calls["sleep"] == [0.2, 0.2, 0.2]
    assert proc.terminated is True


def test_open_cookies_user_agent():
    d, t, proc, calls = make(
        {"Storage.getCookies": {"cookies": [{"name": "cf_clearance", "value": "v"}]}, 
         "Browser.getVersion": {"userAgent": "Mozilla/5.0 Edg/130"}}
    )
    
    d.open("https://example.com/x")
    assert t.sent[0] == {"id": 1, "method": "Target.createTarget", "params": {"url": "https://example.com/x"}}
    
    assert d.cookies() == [{"name": "cf_clearance", "value": "v"}]
    assert d.user_agent() == "Mozilla/5.0 Edg/130"
    
    # Check all commands sent
    assert [m["method"] for m in t.sent] == ["Target.createTarget", "Storage.getCookies", "Browser.getVersion"]


def test_close():
    d, t, proc, calls = make()
    d.close()
    assert t.sent[-1]["method"] == "Browser.close"
    assert t.closed is True
    assert proc.terminated is True