"""A captcha inside a site's own form (DataNodes' Turnstile) is solved in a
real browser on the site's own address and its token goes back to the
plugin (found 2026-11-05 in a live test: "Solve in browser" opened
datanodes.to/download without the download's cookie, so the site showed
"File Not Found", and the button answered with a clearance placeholder
the form could not use)."""
import json

from opengrab import browser_solver as bs
from opengrab.captcha.base import BrowserCaptchaSolver, CaptchaRequest
from opengrab.settings import Settings


class FakePage(bs.BrowserDriver):
    def __init__(self, token_after=2):
        self.opened, self.written, self.closed, self.polls = [], "", False, 0
        self.token_after = token_after

    def open_page(self, url):
        self.opened.append(url)
        return "S1"

    def evaluate(self, session, expression):
        assert session == "S1"
        if expression.startswith("location.origin"):
            self.ready_checks = getattr(self, "ready_checks", 0) + 1
            return self.ready_checks > 1          # the first look sees the blank tab
        if expression.startswith("document.open()"):
            self.written = json.loads(expression[len("document.open();document.write("):-len(");document.close();true")])
            return True
        self.polls += 1
        return "TOKEN-123" if self.polls >= self.token_after else ""

    def close(self):
        self.closed = True


def test_widget_is_drawn_on_the_sites_own_page_and_the_token_returned():
    d = FakePage()
    tok = bs.solve_widget_in_browser("https://datanodes.to/download", "turnstile", "0x4AAAAAAD8U9nktqncPIkBM",
                                     d, timeout=30, sleep=lambda s: None)
    assert tok == "TOKEN-123"
    assert d.opened == ["https://datanodes.to/robots.txt"]       # never /download itself
    assert "cf-turnstile" in d.written and "0x4AAAAAAD8U9nktqncPIkBM" in d.written
    assert "challenges.cloudflare.com/turnstile" in d.written
    assert d.closed


def test_widget_timeout_and_bad_sitekey():
    t = iter(range(0, 1000, 10))
    d = FakePage(token_after=10 ** 6)
    assert bs.solve_widget_in_browser("https://x.example/a", "hcaptcha", "key", d, timeout=30,
                                      clock=lambda: next(t), sleep=lambda s: None) is None
    assert d.closed
    try:
        bs.widget_page("turnstile", "bad key\"><script>", "x.example")
    except ValueError:
        pass
    else:
        raise AssertionError("an unsafe sitekey was accepted")


def test_chain_browser_solver_returns_a_widget_token(monkeypatch):
    calls = []

    def fake(url, kind, key, driver, timeout=None):
        calls.append((url, kind, key, driver))
        return "TOK"
    monkeypatch.setattr(bs, "solve_widget_in_browser", fake)
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True), make=lambda: "DRIVER")
    res = s.solve(CaptchaRequest(kind="turnstile", site_url="https://datanodes.to/download",
                                 recaptcha_sitekey="0xKEY"), timeout_s=5)
    assert (res.ok, res.text) == (True, "TOK")
    assert calls == [("https://datanodes.to/download", "turnstile", "0xKEY", "DRIVER")]


def test_solve_in_browser_button_sends_the_token_for_a_form_captcha(monkeypatch):
    from opengrab.features.captcha import CaptchaService
    from types import SimpleNamespace

    req = SimpleNamespace(id="r1", status="pending", kind="turnstile", sitekey="0xKEY",
                          page_url="https://datanodes.to/download", host="datanodes.to")
    solved = []
    monkeypatch.setattr(bs, "solve_widget_in_browser", lambda *a, **k: "TOK")
    monkeypatch.setattr(bs, "solve_in_browser",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("clearance path used")))
    svc = CaptchaService.__new__(CaptchaService)
    svc.mgr = SimpleNamespace(settings=Settings(), clearance=None)
    svc.srv = SimpleNamespace(features={})
    svc.inbox = SimpleNamespace(get=lambda rid: req)
    svc._busy, svc._keys = set(), {}
    import threading
    svc._lock = threading.Lock()
    svc.solve = lambda rid, answer: solved.append((rid, answer))
    svc._done = lambda: None
    started = []
    monkeypatch.setattr(threading, "Thread",
                        lambda target, name, daemon: SimpleNamespace(start=lambda: started.append(target())))
    code, _ = svc.browser("r1", make=lambda: "DRIVER")
    assert code == 202 and solved == [("r1", "TOK")]


# --- hidden start, shown only when a click is needed (owner decision 2026-11-05) ---

class ShowFake(FakePage):
    def __init__(self, token_after):
        super().__init__(token_after)
        self.shows = 0

    def show(self):
        self.shows += 1


def _ticking():
    t = {"now": 0.0}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += s
    return clock, sleep


def test_show_not_called_when_the_token_arrives_quickly():
    clock, sleep = _ticking()
    d = ShowFake(token_after=3)             # token on the third look, about 2 s in
    assert bs.solve_widget_in_browser("https://x.example/a", "turnstile", "key", d, timeout=45,
                                      clock=clock, sleep=sleep) == "TOKEN-123"
    assert d.shows == 0 and d.closed


def test_show_called_once_when_the_token_is_slow():
    clock, sleep = _ticking()
    d = ShowFake(token_after=20)            # about 19 s in
    assert bs.solve_widget_in_browser("https://x.example/a", "turnstile", "key", d, timeout=45,
                                      clock=clock, sleep=sleep, show_after=8) == "TOKEN-123"
    assert d.shows == 1 and d.closed


def test_show_threshold_is_a_parameter_and_show_still_happens_on_timeout():
    clock, sleep = _ticking()
    d = ShowFake(token_after=10 ** 6)
    assert bs.solve_widget_in_browser("https://x.example/a", "turnstile", "key", d, timeout=20,
                                      clock=clock, sleep=sleep, show_after=2) is None
    assert d.shows == 1 and d.closed


def test_base_driver_show_is_a_no_op():
    assert bs.BrowserDriver().show() is None


def test_hidden_flag_adds_the_off_screen_launch():
    from opengrab.cdp import browser_args
    plain = browser_args("e.exe", 9222, "D:/prof")
    hidden = browser_args("e.exe", 9222, "D:/prof", hidden=True)
    assert not any(a.startswith("--window-position") for a in plain)
    assert "--window-position=-2400,-2400" in hidden and "--window-size=480,640" in hidden
    assert hidden[-1] == "about:blank" and "headless" not in " ".join(hidden)


def test_cdp_driver_hidden_launches_off_screen_and_show_moves_it_on_screen():
    launched = []

    class Proc:
        def poll(self): return None
        def terminate(self): pass

    calls = []

    class Client:
        def __init__(self, transport): pass
        def call(self, method, params=None, session_id=None, timeout=None):
            calls.append((method, params, session_id))
            return {"targetId": "T1", "sessionId": "S1", "windowId": 7}
        def close(self): pass

    import opengrab.cdp as cdp
    orig = cdp.CdpClient
    cdp.CdpClient = Client
    try:
        d = bs.CdpDriver(exe="e.exe", find=lambda: "e.exe", popen=lambda a: launched.append(a) or Proc(),
                         discover=lambda port: "ws://x", transport_factory=lambda ws: None, hidden=True)
        d.show()                            # nothing opened yet: no-op
        assert calls == []
        d.open_page("https://x.example/robots.txt")
        d.show()
        d.show()                            # only once
    finally:
        cdp.CdpClient = orig
    assert "--window-position=-2400,-2400" in launched[0]
    methods = [c[0] for c in calls]
    assert methods.count("Browser.setWindowBounds") == 1
    bounds = next(c[1] for c in calls if c[0] == "Browser.setWindowBounds")
    assert bounds["windowId"] == 7 and bounds["bounds"]["windowState"] == "normal" and bounds["bounds"]["left"] >= 0
    assert "Target.activateTarget" in methods and "Page.bringToFront" in methods


def test_cdp_driver_not_hidden_never_moves_the_window():
    class Proc:
        def poll(self): return None
        def terminate(self): pass
    launched, calls = [], []

    class Client:
        def __init__(self, transport): pass
        def call(self, method, params=None, session_id=None, timeout=None):
            calls.append(method)
            return {"targetId": "T1", "sessionId": "S1"}
        def close(self): pass
    import opengrab.cdp as cdp
    orig = cdp.CdpClient
    cdp.CdpClient = Client
    try:
        d = bs.CdpDriver(exe="e.exe", find=lambda: "e.exe", popen=lambda a: launched.append(a) or Proc(),
                         discover=lambda port: "ws://x", transport_factory=lambda ws: None)
        d.open_page("https://x.example/")
        d.show()
    finally:
        cdp.CdpClient = orig
    assert not any(a.startswith("--window-position") for a in launched[0])
    assert "Browser.setWindowBounds" not in calls


def test_automatic_widget_solve_asks_for_a_hidden_driver(monkeypatch):
    made = []
    monkeypatch.setattr(bs, "make_driver", lambda **kw: made.append(kw) or "DRIVER")
    monkeypatch.setattr(bs, "solve_widget_in_browser", lambda *a, **k: "TOK")
    res = BrowserCaptchaSolver(Settings()).solve(
        CaptchaRequest(kind="turnstile", site_url="https://x.example/a", recaptcha_sitekey="0xKEY"), timeout_s=5)
    assert res.ok and made == [{"hidden": True}]
