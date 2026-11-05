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
