import pytest
from opengrab import browser_solver
from opengrab.settings import Settings
from opengrab.captcha.base import BrowserCaptchaSolver, CaptchaRequest, AudioCaptchaSolver, TwoCaptchaSolver
from opengrab.captcha.chain import SolverChain


class FakeStore:
    def __init__(self):
        self.items = []

    def put(self, c):
        self.items.append(c)


def record(calls, result):
    def fake(url, store, driver, timeout=None):
        calls.append((url, store, driver, timeout))
        return result
    return fake


def test_browser_disabled_by_default():
    assert BrowserCaptchaSolver(Settings()).enabled() is False


def test_browser_enabled_when_set():
    assert BrowserCaptchaSolver(Settings(captcha_browser_enabled=True)).enabled() is True


def test_browser_priority_and_kind():
    assert BrowserCaptchaSolver.kinds == ("turnstile",)
    assert BrowserCaptchaSolver.priority == 30
    assert AudioCaptchaSolver.priority < BrowserCaptchaSolver.priority < TwoCaptchaSolver.priority


def test_browser_solve_ok(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_solver, "solve_in_browser", record(calls, object()))
    store = FakeStore()
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True), store=store, make=lambda: "DRIVER")
    res = s.solve(CaptchaRequest(kind="turnstile", site_url="https://example.com/x"), timeout_s=5)
    assert res.ok is True
    assert res.text == "__CLEARANCE__"
    assert res.provider == "browser"
    assert calls == [("https://example.com/x", store, "DRIVER", 5)]


def test_browser_timeout(monkeypatch):
    monkeypatch.setattr(browser_solver, "solve_in_browser", record([], None))
    store = FakeStore()
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True), store=store, make=lambda: "D")
    res = s.solve(CaptchaRequest(kind="turnstile", site_url="https://example.com/x"), timeout_s=5)
    assert res.ok is False
    assert res.error == "timeout"


def test_browser_no_site_url():
    res = BrowserCaptchaSolver(Settings(), store=FakeStore(), make=lambda: "D").solve(CaptchaRequest(kind="turnstile"))
    assert res.ok is False
    assert res.error == "no site url"


def test_browser_no_store():
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True))
    res = s.solve(CaptchaRequest(kind="turnstile", site_url="https://example.com/x"))
    assert res.ok is False
    assert res.error == "no clearance store"


def test_browser_store_from_extra(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_solver, "solve_in_browser", record(calls, object()))
    store = FakeStore()
    req = CaptchaRequest(kind="turnstile", site_url="https://example.com/", extra={"clearance_store": store})
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True), make=lambda: "D")
    res = s.solve(req)
    assert calls[0][1] is store


def test_browser_unavailable(monkeypatch):
    def boom():
        raise browser_solver.BrowserSolverUnavailable("nope")
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True), make=boom)
    res = s.solve(CaptchaRequest(kind="turnstile", site_url="https://example.com/x"))
    assert res.ok is False
    assert res.error == "nope"


def test_browser_engine_camoufox(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_solver, "solve_in_browser", record(calls, object()))
    monkeypatch.setattr(browser_solver, "make_stealth_driver", lambda: "STEALTH")
    monkeypatch.setattr(browser_solver, "make_driver", lambda: "EDGE")
    store = FakeStore()
    s = BrowserCaptchaSolver(Settings(captcha_browser_engine="camoufox"), store=store)
    res = s.solve(CaptchaRequest(kind="turnstile", site_url="https://example.com/x"))
    assert calls[0][2] == "STEALTH"


def test_browser_engine_edge_default(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_solver, "solve_in_browser", record(calls, object()))
    monkeypatch.setattr(browser_solver, "make_stealth_driver", lambda: "STEALTH")
    monkeypatch.setattr(browser_solver, "make_driver", lambda: "EDGE")
    store = FakeStore()
    s = BrowserCaptchaSolver(Settings(), store=store)
    res = s.solve(CaptchaRequest(kind="turnstile", site_url="https://example.com/x"))
    assert calls[0][2] == "EDGE"


def test_chain_orders_browser_before_manual():
    chain = SolverChain(Settings(captcha_browser_enabled=True))
    ids = [s.id for s in chain._ordered("turnstile")]
    assert ids[0] == "browser"
    assert ids[-1] == "manual"
    assert "browser" in [s.id for s in chain.solvers]