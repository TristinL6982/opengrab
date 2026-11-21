"""Live-test faults: a rate limit failed 13 of 14 links for good, a web page was
saved as the file, a host's "file is offline" fell through to the catch-all
plugins, and a lookup cut short by closing the app was lost.

Everything is offline: fake plugins, a local file server, and a fake clock
(no test waits for real minutes)."""
import threading
import time

import pytest

from opengrab.engine.downloader import LinkDownload, WebPageError, looks_like_html, web_page_expected
from opengrab.engine.manager import RETRY_LATER_MAX, DownloadManager
from opengrab.models import Link, LinkStatus
from opengrab.plugins.base import PluginError, ResolveResult, RetryLater, SitePlugin
from opengrab.settings import Settings
from tests.helpers.apiserver import MASTER, running_server
from tests.helpers.filesite import FileSite

HTML = b"<!DOCTYPE html><html><head><title>File page</title></head><body>rate limited</body></html>" + b" " * 3000


def _wait(pred, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Host(SitePlugin):
    """A site plugin driven by a script: each resolve() takes the next step
    (a RetryLater/PluginError instance to raise, or a URL to hand out)."""
    id, name, site, origin = "filehost", "File host", "files.example", "builtin"

    def __init__(self, steps=(), default=None):
        self.steps, self.default, self.calls = list(steps), default, 0
        self.lock = threading.Lock()

    def grab(self, url, ctx):
        raise NotImplementedError

    def resolve(self, url, ctx):
        with self.lock:
            self.calls += 1
            step = self.steps.pop(0) if self.steps else self.default
        if isinstance(step, Exception):
            raise step
        return ResolveResult(url=step, filename="f.bin")


class CatchAll(SitePlugin):
    id, name, site, origin, catch_all = "catchall", "Catch-all", "", "builtin", True
    called = 0

    def supports(self, url):
        return True

    def grab(self, url, ctx):
        raise NotImplementedError

    def resolve(self, url, ctx):
        CatchAll.called += 1
        raise PluginError("catch-all had a go")


def _start_with(m, srv, plugins):
    """Start a manager whose registry holds only these plugins from the first tick."""
    m.registry.set_user_dir(srv.tmp / "noplugins")
    m.registry.scan = lambda *a, **k: m.registry.plugins.update(plugins) or 1
    m.start()


def _files(srv):
    return [p.name for p in (srv.tmp / "downloads").rglob("*") if p.is_file()]


# ---------------------------------------------------------------- 1. wait and retry
def test_rate_limited_links_wait_instead_of_failing_and_then_download():
    site = FileSite()
    try:
        good = site.add("/ok/f.bin", b"z" * 4000)
        with running_server() as srv:
            m, clock = srv.manager, Clock()
            m._now = clock
            host = Host(default=RetryLater("Rootz rate limit: wait 2 minutes", 120))
            m.registry.plugins = {"filehost": host}
            links = [m.add_url(f"https://files.example/d/{i}") for i in range(14)]
            _wait(lambda: all(m.get(l.id).extra.get("retry_at") for l in links))
            t0 = clock.t
            for l in links:
                got = m.get(l.id)
                assert got.status == LinkStatus.WAITING and got.error == ""
                assert got.extra["retry_count"] == 1 and got.extra["retry_reason"].startswith("Rootz rate limit")
                assert t0 <= got.extra["retry_at"] <= t0 + 120
            m._retry_tick()
            assert all(m.get(l.id).status == LinkStatus.WAITING for l in links)   # not due yet
            host.default = good
            clock.advance(121)
            _wait(lambda: all(m.get(l.id).status == LinkStatus.FINISHED for l in links))
            assert all("retry_at" not in m.get(l.id).extra and "retry_count" not in m.get(l.id).extra
                       for l in links)
    finally:
        site.close()


def test_gives_up_with_a_normal_error_after_twenty_automatic_retries():
    with running_server() as srv:
        m, clock = srv.manager, Clock()
        m._now = clock
        m.registry.plugins = {"filehost": Host(default=RetryLater("busy", 30))}
        link = m.add_url("https://files.example/d/x")
        for n in range(1, RETRY_LATER_MAX + 1):
            _wait(lambda: m.get(link.id).extra.get("retry_count") == n and m.get(link.id).extra.get("retry_at"))
            assert m.get(link.id).status == LinkStatus.WAITING
            clock.advance(31)
            m._retry_tick()
        got = _wait(lambda: m.get(link.id).status == LinkStatus.ERROR and m.get(link.id))
        assert "20 automatic retries" in got.error and "busy" in got.error
        assert "retry_at" not in got.extra


def test_pause_stop_and_remove_cancel_the_pending_retry():
    with running_server() as srv:
        m, clock = srv.manager, Clock()
        m._now = clock
        host = Host(default=RetryLater("busy", 60))
        m.registry.plugins = {"filehost": host}
        a, b, c = (m.add_url(f"https://files.example/d/{i}") for i in "abc")
        _wait(lambda: all(m.get(l.id).extra.get("retry_at") for l in (a, b, c)))
        m.pause_download(a.id)
        m.stop_download(b.id)
        m.remove_link(c.id)
        calls = host.calls
        for l in (a, b):
            assert m.get(l.id).status == LinkStatus.PAUSED and "retry_at" not in m.get(l.id).extra
        clock.advance(600)
        m._retry_tick()
        time.sleep(0.3)
        assert host.calls == calls and m.get(c.id) is None
        assert all(m.get(l.id).status == LinkStatus.PAUSED for l in (a, b))


def test_the_wait_survives_an_app_restart():
    site = FileSite()
    try:
        good = site.add("/ok/f.bin", b"q" * 3000)
        with running_server() as srv:
            m1, clock = srv.manager, Clock()
            m1._now = clock
            m1.registry.plugins = {"filehost": Host(default=RetryLater("busy", 300))}
            link = m1.add_url("https://files.example/d/r")
            _wait(lambda: m1.get(link.id).extra.get("retry_at"))
            at = m1.get(link.id).extra["retry_at"]
            m1.shutdown()

            m2 = DownloadManager(Settings(download_dir=str(srv.tmp / "downloads"), api_token=MASTER,
                                          captcha_browser_enabled=False))
            m2._now = clock
            try:
                _start_with(m2, srv, {"filehost": Host(default=good)})
                got = m2.get(link.id)
                assert got.status == LinkStatus.WAITING and got.extra["retry_at"] == at
                time.sleep(1.5)                       # the wait is not skipped on load
                assert m2.get(link.id).status == LinkStatus.WAITING
                clock.advance(301)                    # the remainder has passed
                _wait(lambda: m2.get(link.id).status == LinkStatus.FINISHED)
            finally:
                m2.shutdown()
    finally:
        site.close()


def test_a_wait_that_ended_while_the_app_was_closed_is_retried_on_start():
    site = FileSite()
    try:
        good = site.add("/ok/f.bin", b"w" * 3000)
        with running_server() as srv:
            m1, clock = srv.manager, Clock()
            m1._now = clock
            m1.registry.plugins = {"filehost": Host(default=RetryLater("busy", 60))}
            link = m1.add_url("https://files.example/d/s")
            _wait(lambda: m1.get(link.id).extra.get("retry_at"))
            m1.shutdown()
            clock.advance(3600)
            m2 = DownloadManager(Settings(download_dir=str(srv.tmp / "downloads"), api_token=MASTER,
                                          captcha_browser_enabled=False))
            m2._now = clock
            try:
                _start_with(m2, srv, {"filehost": Host(default=good)})
                _wait(lambda: m2.get(link.id).status == LinkStatus.FINISHED)
            finally:
                m2.shutdown()
    finally:
        site.close()


def test_retry_later_from_the_new_link_request_keeps_the_link_waiting():
    site = FileSite()
    try:
        dead = site.base + "/gone/f.bin"          # 404 at download time
        good = site.add("/ok/f.bin", b"k" * 2000)
        with running_server() as srv:
            m, clock = srv.manager, Clock()
            m._now = clock
            host = Host(steps=[dead, RetryLater("Rootz rate limit: wait 1 minutes", 60)], default=good)
            m.registry.plugins = {"filehost": host}
            link = m.add_url("https://files.example/d/n")
            got = _wait(lambda: m.get(link.id).extra.get("retry_at") and m.get(link.id))
            assert got.status == LinkStatus.WAITING and got.error == "" and "link_refreshed" not in got.extra
            clock.advance(61)
            _wait(lambda: m.get(link.id).status == LinkStatus.FINISHED)
    finally:
        site.close()


def test_every_builtin_rate_limit_answer_is_a_retry_later():
    from opengrab.plugins.builtin import _megacore as mc
    for code in (-3, -4, -6, -18, -19):
        e = mc.mega_error(code)
        assert isinstance(e, RetryLater) and e.seconds >= 1 and "code" in str(e)
    q = mc.mega_error(mc.EOVERQUOTA, 7500)
    assert isinstance(q, RetryLater) and q.seconds == 7500 and "wait 125 minutes" in q.message
    assert not isinstance(mc.mega_error(-9), RetryLater)
    assert RetryLater("x", 0).seconds == 1 and RetryLater("x", "30").seconds == 30


def test_rootz_raises_retry_later_with_the_hosts_seconds(monkeypatch):
    from tests.regression.test_sites_hosts_a import RZ_OK, Resp, _rz_routes, ctx_for
    from opengrab.plugins.builtin import rootz
    monkeypatch.setattr(rootz, "sleep", lambda s: None)
    long = Resp(302, "", headers={"Location": "/d/BHXge?error=rate_limited&retry_after=1800"})
    with pytest.raises(RetryLater) as e:
        rootz.RootzPlugin().resolve("https://www.rootz.so/d/BHXge", ctx_for(_rz_routes(RZ_OK, head=long)))
    assert e.value.seconds == 1800 and "wait 30 minutes" in str(e.value)


# ---------------------------------------------------------------- 2. a web page is not the file
def test_html_page_is_not_saved_as_the_file_and_the_host_is_asked_for_a_new_link():
    site = FileSite()
    try:
        page = site.add("/f/a.rar", HTML)
        site.ctypes["/f/a.rar"] = "text/html; charset=utf-8"
        with running_server() as srv:
            m, clock = srv.manager, Clock()
            m._now = clock
            host = Host(steps=[page, RetryLater("Rootz rate limit: wait 2 minutes", 120)])
            m.registry.plugins = {"filehost": host}
            link = m.add_url("https://files.example/d/a")
            got = _wait(lambda: m.get(link.id).extra.get("retry_at") and m.get(link.id))
            assert host.calls == 2 and got.status == LinkStatus.WAITING
            assert _files(srv) == []                      # no HTML under the file's name, no .part
    finally:
        site.close()


def test_html_body_is_caught_even_when_the_server_says_octet_stream():
    site = FileSite()
    try:
        page = site.add("/f/a.rar", HTML)             # FileSite answers application/octet-stream
        with running_server() as srv:
            m = srv.manager
            host = Host(default=page)
            m.registry.plugins = {"filehost": host}
            link = m.add_url("https://files.example/d/a")
            got = _wait(lambda: m.get(link.id).status == LinkStatus.ERROR and m.get(link.id))
            assert got.error.startswith("the site sent a web page instead of the file")
            assert host.calls == 2                        # asked for a new link once, then failed
            assert _files(srv) == []
    finally:
        site.close()


def test_html_for_a_plain_file_url_without_a_site_plugin_fails():
    site = FileSite()
    try:
        url = site.add("/f/b.zip", HTML)
        site.ctypes["/f/b.zip"] = "text/html"
        with running_server() as srv:
            link = srv.manager.add_url(url)
            got = _wait(lambda: srv.manager.get(link.id).status == LinkStatus.ERROR and srv.manager.get(link.id))
            assert got.plugin == "direct" and got.error == "the site sent a web page instead of the file"
            assert _files(srv) == []
    finally:
        site.close()


def test_legitimate_html_downloads_still_work():
    site = FileSite()
    try:
        page = site.add("/f/page.html", HTML)
        site.ctypes["/f/page.html"] = "text/html"

        class Pages(Host):
            def resolve(self, url, ctx):
                return ResolveResult(url=page, filename="page.html", size=len(HTML))

        with running_server() as srv:
            srv.manager.registry.plugins = {"filehost": Pages()}
            link = srv.manager.add_url("https://files.example/d/p")
            got = _wait(lambda: srv.manager.get(link.id).status in (LinkStatus.FINISHED, LinkStatus.ERROR)
                        and srv.manager.get(link.id))
            assert got.status == LinkStatus.FINISHED, got.error
            assert _files(srv) == ["page.html"]
    finally:
        site.close()


def test_web_page_rules():
    assert web_page_expected("page.HTML") and web_page_expected("x.php") and web_page_expected("a.aspx")
    assert not web_page_expected("a.rar", "generic") and not web_page_expected("b.zip", "")
    assert web_page_expected("download", "generic") and web_page_expected("", "")      # a plain page URL
    assert not web_page_expected("download", "rootz")                                  # a host promised a file
    assert looks_like_html(b"\xef\xbb\xbf  <!doctype HTML><html>") and looks_like_html(b"<html lang=en>")
    assert not looks_like_html(b"Rar!\x1a\x07") and not looks_like_html(b"PK\x03\x04<html>")


def test_html_much_smaller_than_the_expected_size_is_rejected_even_for_a_page_name(tmp_path):
    site = FileSite()
    try:
        url = site.add("/f/page.html", HTML)
        link = Link(url=url, filename="page.html", size=5_000_000, plugin="filehost")
        with pytest.raises(WebPageError):
            LinkDownload(link, url, tmp_path, settings=Settings(download_dir=str(tmp_path))).run()
        assert list(tmp_path.iterdir()) == []
    finally:
        site.close()


# ---------------------------------------------------------------- 3. a host's answer is final
def test_a_site_plugins_error_stops_the_search_but_catch_alls_still_fall_through():
    with running_server() as srv:
        m = srv.manager
        CatchAll.called = 0
        host = Host(default=PluginError("this file does not exist anymore"))
        m.registry.plugins = {"filehost": host, "catchall": CatchAll()}
        assert [p.id for p in m.registry.for_url("https://files.example/d/1")] == ["filehost", "catchall"]
        link = m.add_url("https://files.example/d/1")
        got = _wait(lambda: m.get(link.id).status == LinkStatus.ERROR and m.get(link.id))
        assert got.error == "this file does not exist anymore" and got.plugin == "filehost"
        assert CatchAll.called == 0

        class Second(CatchAll):
            id = "catchall2"

        m.registry.plugins = {"catchall": CatchAll(), "catchall2": Second()}
        link2 = m.add_url("https://other.example/x")
        _wait(lambda: m.get(link2.id).status == LinkStatus.ERROR)
        assert CatchAll.called >= 2                    # catch-all to catch-all still falls through


def test_retry_later_also_stops_the_search():
    with running_server() as srv:
        m, clock = srv.manager, Clock()
        m._now = clock
        CatchAll.called = 0
        m.registry.plugins = {"filehost": Host(default=RetryLater("busy", 30)), "catchall": CatchAll()}
        link = m.add_url("https://files.example/d/1")
        got = _wait(lambda: m.get(link.id).extra.get("retry_at") and m.get(link.id))
        assert got.status == LinkStatus.WAITING and got.plugin == "filehost" and CatchAll.called == 0


def test_the_builtin_catch_alls_are_marked():
    from opengrab.engine.manager import DownloadManager as DM
    from opengrab.plugins.registry import PluginRegistry
    reg = PluginRegistry()
    reg.scan(force=True)
    marked = {pid for pid, p in reg.plugins.items() if DM._is_catch_all(p)}
    assert {"direct", "generic", "multilink", "ytbridge", "gallerydl"} <= marked
    assert not ({"rootz", "gofile", "onefichier", "fuckingfast", "mega"} & marked)


# ---------------------------------------------------------------- 4. interrupted lookups resume
def test_a_lookup_cut_short_by_closing_the_app_runs_again_on_start():
    site = FileSite()
    try:
        good = site.add("/ok/f.bin", b"r" * 3000)
        with running_server() as srv:
            m1 = srv.manager
            link = Link(url="https://files.example/d/cut", status=LinkStatus.RESOLVING)
            with m1._lock:
                m1._links[link.id] = link
            m1.shutdown()                          # saves the link as RESOLVING
            m2 = DownloadManager(Settings(download_dir=str(srv.tmp / "downloads"), api_token=MASTER,
                                          captcha_browser_enabled=False))
            try:
                _start_with(m2, srv, {"filehost": Host(default=good)})
                got = _wait(lambda: m2.get(link.id).status == LinkStatus.FINISHED and m2.get(link.id))
                assert "re-add" not in got.error
            finally:
                m2.shutdown()
    finally:
        site.close()


def test_waiting_for_the_site_does_not_leave_threads_behind():
    def settle(limit, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if threading.active_count() <= limit:
                return True
            time.sleep(0.1)
        return threading.active_count() <= limit

    with running_server():
        pass
    settle(0)
    base = threading.active_count()
    with running_server() as srv:
        srv.manager.registry.plugins = {"filehost": Host(default=RetryLater("busy", 600))}
        link = srv.manager.add_url("https://files.example/d/t")
        _wait(lambda: srv.manager.get(link.id).extra.get("retry_at"))
    assert settle(base + 1), sorted(t.name for t in threading.enumerate())

# ---------------------------------------------------------------- shared back-off per host
class WindowHost(SitePlugin):
    """Hands out one download start per 60 s window; every other lookup is told to wait."""
    id, name, site, origin = "filehost", "File host", "files.example", "builtin"

    def __init__(self, clock, url):
        self.clock, self.url, self.open_at, self.calls = clock, url, 0.0, 0
        self.lock = threading.Lock()

    def grab(self, url, ctx):
        raise NotImplementedError

    def resolve(self, url, ctx):
        with self.lock:
            self.calls += 1
            if self.clock() < self.open_at:
                raise RetryLater("Rootz rate limit: wait 1 minutes", 60)
            self.open_at = self.clock() + 60
        return ResolveResult(url=self.url, filename="f.bin")


def test_links_of_one_host_share_the_hosts_answer_and_do_not_hammer_it():
    site = FileSite()
    try:
        good = site.add("/ok/f.bin", b"s" * 1500)
        with running_server() as srv:
            m, clock = srv.manager, Clock()
            m._now = clock
            host = WindowHost(clock, good)
            m.registry.plugins = {"filehost": host}
            links = [m.add_url(f"https://files.example/d/{i}") for i in range(30)]

            def settled():
                for l in links:
                    g = m.get(l.id)
                    if g.status == LinkStatus.RESOLVING:
                        return False
                    if g.status == LinkStatus.WAITING and not (g.extra.get("retry_at") and g.extra["retry_at"] > clock.t):
                        return False
                return True

            _wait(settled)
            assert host.calls == 30                      # the first burst: nothing is known yet
            cycles, per_cycle = 0, []
            while not all(m.get(l.id).status == LinkStatus.FINISHED for l in links):
                cycles += 1
                assert cycles <= 60
                before = host.calls
                clock.advance(61)
                deadline = time.time() + 30
                while time.time() < deadline:
                    m._retry_tick()
                    time.sleep(0.02)
                    if settled():
                        break
                per_cycle.append(host.calls - before)
                _wait(lambda: all(m.get(l.id).status != LinkStatus.DOWNLOADING for l in links))
            assert max(m.get(l.id).extra.get("retry_count", 0) for l in links) <= 4
            assert max(per_cycle) <= 3, per_cycle
    finally:
        site.close()


def test_hosts_do_not_block_each_other_but_one_host_is_looked_up_one_link_at_a_time():
    site = FileSite()
    try:
        good = site.add("/ok/f.bin", b"u" * 1500)
        gate, started = threading.Event(), []

        class Slow(Host):
            def resolve(self, url, ctx):
                started.append(url)
                gate.wait(20)
                return ResolveResult(url=good, filename="f.bin")

        class Fast(Host):
            id, site = "otherhost", "other.example"

            def resolve(self, url, ctx):
                return ResolveResult(url=good, filename="g.bin")

        with running_server() as srv:
            m, clock = srv.manager, Clock()
            m._now = clock
            m.registry.plugins = {"filehost": Slow(), "otherhost": Fast()}
            due = clock.t - 1
            made = []
            for i, (plugin, host) in enumerate([("filehost", "files.example"), ("filehost", "files.example"),
                                                ("otherhost", "other.example")]):
                l = Link(url=f"https://{host}/d/{i}", plugin=plugin, status=LinkStatus.WAITING,
                         extra={"retry_at": due, "retry_count": 1}, created=clock.t + i)
                with m._lock:
                    m._links[l.id] = l
                made.append(l)
            m._retry_tick()
            a1, a2, b1 = (m.get(l.id) for l in made)
            assert a1.status == LinkStatus.RESOLVING            # lowest queue position goes first
            assert a2.status == LinkStatus.WAITING and "retry_at" in a2.extra
            _wait(lambda: m.get(b1.id).status == LinkStatus.FINISHED)   # the other host is not held up
            m._retry_tick()
            assert m.get(a2.id).status == LinkStatus.WAITING      # still one lookup at a time
            gate.set()
            _wait(lambda: m.get(a1.id).status == LinkStatus.FINISHED and m.get(a2.id).status == LinkStatus.FINISHED)
            assert len(started) == 2
    finally:
        site.close()


def test_a_hosts_new_wait_pushes_out_its_other_waiting_links_without_costing_a_retry():
    with running_server() as srv:
        m, clock = srv.manager, Clock()
        m._now = clock
        m.registry.plugins = {}
        near = Link(url="https://files.example/d/1", plugin="filehost", status=LinkStatus.WAITING,
                    extra={"retry_at": clock.t + 5, "retry_count": 2})
        other = Link(url="https://other.example/d/1", plugin="filehost", status=LinkStatus.WAITING,
                     extra={"retry_at": clock.t + 5, "retry_count": 2})
        mine = Link(url="https://files.example/d/2", plugin="filehost", status=LinkStatus.RESOLVING)
        with m._lock:
            for l in (near, other, mine):
                m._links[l.id] = l
        m._retry_later(mine, RetryLater("busy", 300))
        assert near.extra["retry_at"] == mine.extra["retry_at"] == clock.t + 300 and near.extra["retry_count"] == 2
        assert other.extra["retry_at"] == clock.t + 5          # another host keeps its own time


# ---------------------------------------------------------------- the probe is not too strict
def _download_via_host(srv, url, name):
    class One(Host):
        def resolve(self, u, ctx):
            return ResolveResult(url=url, filename=name)

    srv.manager.registry.plugins = {"filehost": One()}
    link = srv.manager.add_url("https://files.example/d/x")
    return _wait(lambda: srv.manager.get(link.id).status in (LinkStatus.FINISHED, LinkStatus.ERROR)
                 and srv.manager.get(link.id))


def test_a_large_binary_sent_as_text_html_is_saved():
    site = FileSite()
    try:
        data = b"Rar!\x1a\x07\x00" + bytes(range(256)) * 9000          # about 2.3 MiB
        url = site.add("/f/big.rar", data)
        site.ctypes["/f/big.rar"] = "text/html"
        with running_server() as srv:
            got = _download_via_host(srv, url, "big.rar")
            assert got.status == LinkStatus.FINISHED, got.error
            assert (srv.tmp / "downloads" / "big.rar").read_bytes() == data
    finally:
        site.close()


def test_an_attachment_with_an_html_content_type_is_saved():
    site = FileSite()
    try:
        data = b"Rar!\x1a\x07\x00" + b"\x01" * 3000
        url = site.add("/f/small.rar", data)
        site.ctypes["/f/small.rar"] = "text/html"
        site.extra_headers["/f/small.rar"] = {"Content-Disposition": 'attachment; filename="small.rar"'}
        with running_server() as srv:
            got = _download_via_host(srv, url, "small.rar")
            assert got.status == LinkStatus.FINISHED, got.error
            assert (srv.tmp / "downloads" / "small.rar").read_bytes() == data
    finally:
        site.close()


def test_a_small_html_page_for_a_rar_name_is_refused_at_the_probe(tmp_path):
    site = FileSite()
    try:
        url = site.add("/f/a.rar", HTML)
        site.ctypes["/f/a.rar"] = "text/html"
        link = Link(url=url, filename="a.rar", plugin="filehost")
        with pytest.raises(WebPageError):
            LinkDownload(link, url, tmp_path, settings=Settings(download_dir=str(tmp_path))).run()
        assert not any(cmd == "GET" for cmd, _p, _h in site.requests)   # refused before any body was fetched
        assert list(tmp_path.iterdir()) == []
    finally:
        site.close()
