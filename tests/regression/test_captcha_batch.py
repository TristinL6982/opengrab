"""One browser window for many widget captchas.

A release with ~30 links on one host needs 30 tokens (one per link; Turnstile
tokens work once). They come from ONE page with N widgets, and only one
browser runs at a time. Everything here uses fakes: no browser is started."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from opengrab import browser_solver as bs
from opengrab.captcha.base import BrowserCaptchaSolver, CaptchaRequest
from opengrab.captcha.batcher import WidgetBatcher
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings
from tests.helpers.apiserver import req, running_server

STATIC = bs.__file__.replace("browser_solver.py", "ui/static/")


# ---------------------------------------------------------------- the batch page
class FakeBatchPage(bs.BrowserDriver):
    """Plays a browser whose widgets finish `per_poll` at a time."""

    def __init__(self, per_poll=2, slow_polls=0):
        self.html, self.closed, self.shows, self.opened = "", False, 0, []
        self.per_poll, self.slow_polls, self.polls, self.tokens, self.ready_checks = per_poll, slow_polls, 0, [], 0

    def open_page(self, url):
        self.opened.append(url)
        return "S1"

    def show(self):
        self.shows += 1

    def evaluate(self, session, expression):
        if expression.startswith("location.origin"):
            self.ready_checks += 1
            return self.ready_checks > 1
        if expression.startswith("document.open()"):
            self.html = json.loads(expression[len("document.open();document.write("):-len(");document.close();true")])
            return True
        m = re.fullmatch(r"JSON\.stringify\(\(window\.__ogTokens\|\|\[\]\)\.slice\((\d+)\)\)", expression)
        assert m, expression
        self.polls += 1
        total = self.html.count("id='ogw")
        if self.polls > self.slow_polls:
            while len(self.tokens) < min(total, len(self.tokens) + self.per_poll):
                self.tokens.append(f"TOK-{len(self.tokens)}")
        return json.dumps(self.tokens[int(m.group(1)):])

    def close(self):
        self.closed = True


def _ticking():
    t = {"now": 0.0}
    return (lambda: t["now"]), (lambda s: t.__setitem__("now", t["now"] + s))


def test_batch_renders_n_widgets_and_hands_over_each_token_once():
    d, got = FakeBatchPage(per_poll=2), []
    n = bs.solve_widgets_in_browser("https://datanodes.to/download", "turnstile", "0x4AAAAAAD8U9nktqncPIkBM",
                                    5, d, got.append, timeout=30, sleep=lambda s: None)
    assert n == 5 and got == [f"TOK-{i}" for i in range(5)]       # distinct, in order
    assert len(set(got)) == 5
    assert d.opened == ["https://datanodes.to/robots.txt"]
    assert d.html.count("id='ogw") == 5
    assert "turnstile.render(" in d.html and "render=explicit&onload=__ogReady" in d.html
    assert "0x4AAAAAAD8U9nktqncPIkBM" in d.html and "challenges.cloudflare.com/turnstile" in d.html
    assert "checks done for" in d.html                              # progress heading
    assert d.closed


def test_batch_page_uses_each_widgets_explicit_render_api():
    assert "hcaptcha.render(" in bs.widgets_page("hcaptcha", "key", "x.example", 2)
    assert "js.hcaptcha.com/1/api.js?render=explicit" in bs.widgets_page("hcaptcha", "key", "x.example", 2)
    rc = bs.widgets_page("recaptcha", "key", "x.example", 2)
    assert "grecaptcha.render(" in rc and "recaptcha/api.js?render=explicit" in rc


def test_batch_page_rejects_bad_input():
    for args in [("turnstile", 'bad key"><script>', "x.example", 2), ("nope", "key", "x.example", 2),
                 ("turnstile", "key", "x.example", 0), ("turnstile", "key", "x.example", 10 ** 6)]:
        with pytest.raises(ValueError):
            bs.widgets_page(*args)
    assert "</script>.x" not in bs.widgets_page("turnstile", "key", "a</script>.x", 1).split("<script>", 1)[1].split("</script>")[0]


def test_batch_shows_the_window_only_when_slow():
    clock, sleep = _ticking()
    fast = FakeBatchPage(per_poll=5)
    bs.solve_widgets_in_browser("https://x.example/a", "turnstile", "key", 5, fast, lambda t: None,
                                timeout=60, clock=clock, sleep=sleep, show_after=8)
    assert fast.shows == 0 and fast.closed

    clock, sleep = _ticking()
    slow = FakeBatchPage(per_poll=5, slow_polls=40)                 # nothing for about 20 s
    got = []
    bs.solve_widgets_in_browser("https://x.example/a", "turnstile", "key", 5, slow, got.append,
                                timeout=60, clock=clock, sleep=sleep, show_after=8)
    assert slow.shows == 1 and len(got) == 5 and slow.closed


def test_batch_timeout_returns_what_arrived_and_closes():
    clock, sleep = _ticking()
    d, got = FakeBatchPage(per_poll=1, slow_polls=0), []
    d.per_poll = 0                                                  # never finishes
    assert bs.solve_widgets_in_browser("https://x.example/a", "turnstile", "key", 3, d, got.append,
                                       timeout=20, clock=clock, sleep=sleep) == 0
    assert d.closed and got == []


def test_a_failing_consumer_does_not_stop_the_other_tokens():
    d, got = FakeBatchPage(per_poll=3), []

    def on_token(t):
        got.append(t)
        if t == "TOK-0":
            raise RuntimeError("boom")
    assert bs.solve_widgets_in_browser("https://x.example/a", "turnstile", "key", 3, d, on_token,
                                       timeout=30, sleep=lambda s: None) == 3
    assert len(got) == 3


# -------------------------------------------------------------------- the batcher
class FakeSolve:
    """solve_batch fake: records every call, gives out distinct tokens."""

    def __init__(self, gate=None, delay=0.0, tokens=True):
        self.calls, self.running, self.max_running, self.n = [], 0, 0, 0
        self.gate, self.delay, self.tokens, self.started = gate, delay, tokens, threading.Event()
        self.mx = threading.Lock()

    def __call__(self, page_url, kind, sitekey, count, on_token, hidden):
        with self.mx:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
            self.calls.append((page_url, kind, sitekey, count, hidden))
        self.started.set()
        try:
            if self.gate is not None and len(self.calls) == 1:
                self.gate.wait(10)
            time.sleep(self.delay)
            if self.tokens:
                for _ in range(count):
                    with self.mx:
                        self.n += 1
                        tok = f"T{self.n}"
                    on_token(tok)
        finally:
            with self.mx:
                self.running -= 1


def _request_all(b, n, host="datanodes.to", kind="turnstile", key="K1"):
    pool = ThreadPoolExecutor(n)
    futs = [pool.submit(b.request, host, kind, key, f"https://{host}/dl/{i}") for i in range(n)]
    return pool, futs


def test_30_parked_links_share_one_solve_and_get_different_tokens():
    fake = FakeSolve()
    b = WidgetBatcher(fake, window=2.0)  # wide: 30 threads must all park before it closes on a slow runner
    pool, futs = _request_all(b, 30)
    tokens = [f.result(15) for f in futs]
    pool.shutdown()
    assert len(fake.calls) == 1 and fake.calls[0][3] == 30
    assert fake.calls[0][4] is True                                 # the automatic path starts out of view
    assert len(set(tokens)) == 30 and "" not in tokens              # one token per link, never shared


def test_a_link_parking_mid_batch_joins_the_next_batch():
    gate = threading.Event()
    fake = FakeSolve(gate=gate)
    b = WidgetBatcher(fake, window=0.2)
    pool, first = _request_all(b, 3)
    assert fake.started.wait(10)                                     # batch 1 is running (3 links)
    late_pool, late = _request_all(b, 2)
    time.sleep(0.5)
    assert len(fake.calls) == 1                                      # still only the first window
    gate.set()
    got = [f.result(15) for f in first + late]
    pool.shutdown(); late_pool.shutdown()
    assert [c[3] for c in fake.calls] == [3, 2]
    assert len(set(got)) == 5


def test_only_one_browser_runs_at_a_time_even_for_different_hosts():
    fake = FakeSolve(delay=0.15)
    b = WidgetBatcher(fake, window=0.1)
    pools = []
    futs = []
    for host in ("a.example", "b.example", "c.example"):
        p, f = _request_all(b, 2, host=host)
        pools.append(p); futs += f
    assert all(f.result(20) for f in futs)
    assert len(fake.calls) == 3 and fake.max_running == 1


def test_a_batch_holds_at_most_max_batch_links():
    fake = FakeSolve()
    b = WidgetBatcher(fake, window=2.0, max_batch=40)
    pool, futs = _request_all(b, 45)
    tokens = [f.result(20) for f in futs]
    pool.shutdown()
    assert sorted(c[3] for c in fake.calls) == [5, 40]
    assert len(set(tokens)) == 45


def test_links_of_different_sitekeys_are_not_mixed():
    fake = FakeSolve()
    b = WidgetBatcher(fake, window=0.3)
    p1, f1 = _request_all(b, 2, key="K1")
    p2, f2 = _request_all(b, 3, key="K2")
    assert all(f.result(15) for f in f1 + f2)
    assert sorted((c[2], c[3]) for c in fake.calls) == [("K1", 2), ("K2", 3)]


def test_a_batch_that_gets_nothing_releases_every_link_and_is_not_retried_at_once():
    fake = FakeSolve(tokens=False)
    b = WidgetBatcher(fake, window=0.1, fail_cooldown=30)
    pool, futs = _request_all(b, 4)
    assert [f.result(15) for f in futs] == ["", "", "", ""]
    t = time.monotonic()
    assert b.request("datanodes.to", "turnstile", "K1", "https://datanodes.to/x") == ""
    assert time.monotonic() - t < 1.0 and len(fake.calls) == 1       # parked for a human instead of a second window


def test_a_solver_that_raises_releases_the_links():
    def boom(*a):
        raise bs.BrowserSolverUnavailable("no edge")
    b = WidgetBatcher(boom, window=0.1)
    pool, futs = _request_all(b, 3)
    assert [f.result(15) for f in futs] == ["", "", ""]


def test_run_now_takes_the_same_lock_as_the_automatic_batches():
    fake = FakeSolve(delay=0.2)
    b = WidgetBatcher(fake, window=0.05)
    pool, futs = _request_all(b, 2)
    assert fake.started.wait(10)
    got = []
    assert b.run_now("https://datanodes.to/x", "turnstile", "K1", 3, got.append, hidden=False) == 3
    assert all(f.result(15) for f in futs)
    assert fake.max_running == 1 and fake.calls[-1][4] is False and len(got) == 3


# ----------------------------------------------- the browser solver uses the batcher
def test_chain_solver_asks_the_batcher_and_each_link_gets_its_own_token():
    fake = FakeSolve()
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True), make=lambda: "D")
    s.batcher = WidgetBatcher(fake, window=0.3)
    pool = ThreadPoolExecutor(6)
    futs = [pool.submit(s.solve, CaptchaRequest(kind="turnstile", site_url=f"https://datanodes.to/dl/{i}",
                                                recaptcha_sitekey="0xKEY"), 30) for i in range(6)]
    res = [f.result(15) for f in futs]
    assert all(r.ok for r in res) and len({r.text for r in res}) == 6
    assert len(fake.calls) == 1 and fake.calls[0][3] == 6
    bad = s.solve(CaptchaRequest(kind="turnstile", site_url="https://x.example/", recaptcha_sitekey="bad key!"))
    assert not bad.ok and len(fake.calls) == 1                        # an unsafe key never reaches the browser


def test_solve_batch_validates_before_starting_a_browser(monkeypatch):
    s = BrowserCaptchaSolver(Settings(), make=lambda: pytest.fail("a browser was started"))
    with pytest.raises(ValueError):
        s.solve_batch("https://x.example/", "turnstile", "bad key!", 2, lambda t: None)


def test_clearance_waiters_do_not_reopen_edge_once_a_clearance_exists(monkeypatch):
    opened = []

    class Store:
        has = False

        def get(self, host):
            return object() if self.has else None
    store = Store()

    def fake_solve(url, st, driver, timeout=None):
        opened.append(url)
        st.has = True
        return object()
    monkeypatch.setattr(bs, "solve_in_browser", fake_solve)
    s = BrowserCaptchaSolver(Settings(captcha_browser_enabled=True), store=store, make=lambda: "D")
    s.batcher = WidgetBatcher(FakeSolve())
    pool = ThreadPoolExecutor(5)
    futs = [pool.submit(s.solve, CaptchaRequest(kind="turnstile", site_url="https://cf.example/a"), 5)
            for _ in range(5)]
    assert all(f.result(10).text == "__CLEARANCE__" for f in futs)
    assert len(opened) == 1


# ------------------------------------------- tokens map to links through the manager
def _park(mgr, host, n, kind="turnstile", sitekey="0xKEY"):
    links = []
    for i in range(n):
        l = Link(url=f"https://{host}/f/{i}", status=LinkStatus.CAPTCHA, captcha_kind=kind)
        mgr._links[l.id] = l
        links.append(l)
    inbox = mgr.captcha_inbox.submit(host, kind, f"https://{host}/dl", sitekey=sitekey)
    for l in links:
        l.extra["captcha_request_id"] = inbox.id
    return links, inbox


def test_submit_captcha_retries_each_link_with_exactly_its_token(tmp_path):
    with running_server(start_manager=False) as srv:
        mgr = srv.manager
        links, _ = _park(mgr, "datanodes.to", 30)
        seen, mx = {}, threading.Lock()
        mgr._plugin_for = lambda link: object()
        mgr._ctx_for = lambda plugin: SimpleNamespace(manual_code="")

        def resolve(link, plugin, ctx):
            with mx:
                seen[link.id] = ctx.manual_code
        mgr._resolve = resolve
        for i, l in enumerate(links):
            mgr.give_captcha_token(l, f"TOKEN-{i}")
        deadline = time.time() + 10
        while len(seen) < 30 and time.time() < deadline:
            time.sleep(0.05)
        assert seen == {l.id: f"TOKEN-{i}" for i, l in enumerate(links)}
        assert len(set(seen.values())) == 30


# ----------------------------------------------------------- the verify-all API
def test_verify_all_groups_by_host_kind_and_sitekey_and_counts_links():
    with running_server(start_manager=False) as srv:
        mgr = srv.manager
        a, _ = _park(mgr, "datanodes.to", 7)
        b, _ = _park(mgr, "fuckingfast.co", 4, kind="hcaptcha", sitekey="hkey")
        cf, _ = _park(mgr, "cf.example", 3, kind="cf_js", sitekey=None)           # no widget: not part of it
        calls, mx = [], threading.Lock()

        def fake_solve(page_url, kind, sitekey, count, on_token, hidden):
            with mx:
                calls.append((page_url, kind, sitekey, count, hidden))
            for i in range(count):
                on_token(f"{kind}-{len(calls)}-{i}")
        mgr.captcha_batcher.solve_batch = fake_solve
        given = []
        mgr.give_captcha_token = lambda link, token: given.append((link.id, token))

        st, body = req(srv, "POST", "/api/captcha/verify-all", {})
        assert st == 200 and body["started"] == 11 and body["groups"] == 2, body
        deadline = time.time() + 10
        while len(given) < 11 and time.time() < deadline:
            time.sleep(0.05)
        assert sorted((c[1], c[2], c[3]) for c in calls) == [("hcaptcha", "hkey", 4), ("turnstile", "0xKEY", 7)]
        assert all(c[4] is False for c in calls)                      # the user asked: the window is visible
        assert {g[0] for g in given} == {l.id for l in a + b}         # every waiting widget link, no clearance link
        assert len({g[1] for g in given}) == 11                       # a different token for each
        assert {l.id for l in cf}.isdisjoint({g[0] for g in given})


def test_verify_all_with_nothing_waiting_starts_nothing():
    with running_server(start_manager=False) as srv:
        st, body = req(srv, "POST", "/api/captcha/verify-all", {})
        assert st == 200 and body["started"] == 0 and body["groups"] == 0


def test_verify_all_does_not_start_links_a_run_is_already_working_on():
    with running_server(start_manager=False) as srv:
        mgr = srv.manager
        _park(mgr, "datanodes.to", 3)
        gate = threading.Event()

        def slow(page_url, kind, sitekey, count, on_token, hidden):
            gate.wait(10)
        mgr.captcha_batcher.solve_batch = slow
        st, first = req(srv, "POST", "/api/captcha/verify-all", {})
        st2, second = req(srv, "POST", "/api/captcha/verify-all", {})
        gate.set()
        assert first["started"] == 3 and second["started"] == 0


# ------------------------------------------------------------------------ the UI
def test_settings_checkbox_and_verify_button_exist():
    html = open(STATIC + "index.html", encoding="utf-8").read()
    js = open(STATIC + "app.js", encoding="utf-8").read()
    assert 'id="set-captcha_browser_enabled" type="checkbox"' in html
    assert ("Solve captchas automatically in a real Edge window (starts out of view; "
            "comes forward only if a click is needed)") in html
    assert '"captcha_browser_enabled"' in js.split("const SET_FIELDS", 1)[1].split("];", 1)[0]
    assert 'id="btn-captcha-verify-all"' in html and "/api/captcha/verify-all" in js
    zh = json.loads(open(STATIC + "i18n/zh-CN.json", encoding="utf-8").read())
    for s in ("Verify all", "Nothing is waiting for a check"):
        assert s in zh["strings"]
    assert open(STATIC + "i18n/zh-CN.json", "rb").read().endswith(b"\n")


def test_the_setting_round_trips_through_the_api():
    with running_server() as srv:
        st, s = req(srv, "GET", "/api/settings")
        assert st == 200 and s["captcha_browser_enabled"] is False        # the test helper switches it off
        st, _ = req(srv, "POST", "/api/settings", {"captcha_browser_enabled": True})
        assert st == 200
        assert req(srv, "GET", "/api/settings")[1]["captcha_browser_enabled"] is True
