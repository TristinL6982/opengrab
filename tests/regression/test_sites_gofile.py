"""GoFile: the listing comes from GoFile's own page code in a real browser
(faked here), downloads carry the visitor's accountToken cookie."""
import pytest

from opengrab.plugins.base import LoginRequired, PaywallRequired, PluginError
from opengrab.plugins.builtin import gofile


class FakePage:
    opened = []

    def __init__(self, answer, ua="EdgeUA/1"):
        self.answer, self.ua = answer, ua

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def goto(self, url):
        FakePage.opened.append(url)

    def wait_for(self, expr, timeout=30):
        return True

    def evaluate(self, expr, timeout=60):
        assert "getFolder" in expr
        return self.answer

    def user_agent(self):
        return self.ua


def use(answer, monkeypatch):
    gofile._cache.clear()
    FakePage.opened = []
    monkeypatch.setattr(gofile, "PAGE_FACTORY", lambda: FakePage(answer))


def file_item(i, name, size=1000):
    return {"id": f"f{i}", "type": "file", "name": name, "size": size, "md5": "0" * 31 + str(i),
            "link": f"https://store-eu-par-8.gofile.io/download/web/f{i}/{name}"}


def test_links_it_claims():
    p = gofile.GoFilePlugin()
    assert p.supports("https://gofile.io/d/p9wMsNGq")
    assert p.supports("https://gofile.io/d/AbC123#file=d4269fc7-b6d3")
    assert not p.supports("https://gofile.io/myfiles")
    assert not p.supports("https://example.com/d/abc")


def test_single_file_folder_downloads_directly_with_the_visitor_token(monkeypatch):
    use({"token": "tok123", "data": {"type": "folder", "canAccess": True, "children": {
        "f1": file_item(1, "setup.zip", 12_000)}}}, monkeypatch)
    r = gofile.GoFilePlugin().resolve("https://gofile.io/d/abc", None)
    assert r.url.startswith("https://store-eu-par-8.gofile.io/download/web/f1/")
    assert r.filename == "setup.zip" and r.size == 12_000
    assert r.headers["cookie"] == "accountToken=tok123" and r.headers["user-agent"] == "EdgeUA/1"
    assert r.extra["expected_hash"] == "md5:" + "0" * 31 + "1"
    assert FakePage.opened == ["https://gofile.io/d/abc"]


def test_folder_becomes_one_download_per_file_and_subfolder(monkeypatch):
    use({"token": "t", "data": {"type": "folder", "name": "Pack", "canAccess": True, "children": {
        "f1": file_item(1, "a.bin"), "f2": file_item(2, "b.bin"),
        "s1": {"id": "s1", "type": "folder", "name": "Extras", "code": "XyZ9"}}}}, monkeypatch)
    plugin = gofile.GoFilePlugin()
    r = plugin.resolve("https://gofile.io/d/abc", None)
    assert r.url == "" and r.filename == "Pack"
    assert [c[1] for c in r.children] == ["a.bin", "b.bin", "Extras"]
    assert r.children[2][0] == "https://gofile.io/d/XyZ9"
    child = plugin.resolve(r.children[1][0], None)  # served from the cache: no second browser trip
    assert child.filename == "b.bin" and FakePage.opened == ["https://gofile.io/d/abc"]
    with pytest.raises(PluginError, match="no longer in the GoFile folder"):
        plugin.resolve("https://gofile.io/d/abc#file=gone", None)


def test_access_problems_have_clear_messages(monkeypatch):
    use({"token": "t", "data": {"type": "folder", "canAccess": False, "password": True}}, monkeypatch)
    with pytest.raises(LoginRequired):
        gofile.GoFilePlugin().resolve("https://gofile.io/d/pw", None)
    use({"error": "error-notFound"}, monkeypatch)
    with pytest.raises(PluginError, match="does not exist"):
        gofile.GoFilePlugin().resolve("https://gofile.io/d/gone", None)
    use({"error": "error-notPremium"}, monkeypatch)
    with pytest.raises(PaywallRequired):
        gofile.GoFilePlugin().resolve("https://gofile.io/d/prem", None)
    use({"token": "t", "data": {"type": "folder", "canAccess": True, "children": {}}}, monkeypatch)
    with pytest.raises(PluginError, match="empty"):
        gofile.GoFilePlugin().resolve("https://gofile.io/d/empty", None)


def test_browser_page_reads_values_and_reports_script_errors():
    from opengrab import browserpage

    class FakeClient:
        def __init__(self, ws):
            self.calls = []

        def call(self, method, params=None, timeout=10.0):
            self.calls.append(method)
            if method == "Runtime.evaluate":
                if "boom" in params["expression"]:
                    return {"exceptionDetails": {"exception": {"description": "Error: boom\n at x"}}}
                return {"result": {"value": {"ok": 1}}}
            return {}

        def close(self):
            pass

    class FakeDriver:
        closed = False

        def close(self):
            FakeDriver.closed = True

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    opener = lambda url, timeout=5: Resp(b'[{"type":"page","webSocketDebuggerUrl":"ws://x"}]')
    from unittest.mock import patch
    with patch.object(browserpage.cdp, "CdpClient", FakeClient):
        with browserpage.BrowserPage(driver_factory=FakeDriver, opener=opener,
                                     transport_factory=lambda ws: ws) as page:
            assert page.evaluate("1") == {"ok": 1}
            with pytest.raises(browserpage.BrowserPageError, match="boom"):
                page.evaluate("boom()")
    assert FakeDriver.closed and not browserpage.BrowserPage._busy.locked()


def test_spider_keeps_keys_in_mega_and_gofile_links():
    from opengrab.spider import extract_links
    html = ('<a href="https://mega.nz/file/AbC#keyXYZ">a</a><a href="https://gofile.io/d/abc#file=f1">b</a>'
            '<a href="https://transfer.it/t/xyz#file=n1">c</a><a href="https://example.com/page#top">d</a>')
    assert extract_links(html, "https://src.example/") == [
        "https://mega.nz/file/AbC#keyXYZ", "https://gofile.io/d/abc#file=f1",
        "https://transfer.it/t/xyz#file=n1", "https://example.com/page"]


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


class _ApiCtx:
    """A ctx whose account carries a GoFile API token and whose client
    answers like api.gofile.io."""

    def __init__(self, body, token="premTok"):
        from types import SimpleNamespace
        self.acc = SimpleNamespace(data={"api_token": token}, password="", premium=True)
        self.body, self.calls = body, []
        self.client = SimpleNamespace(get=self._get)

    def _get(self, url, headers=None, **kw):
        self.calls.append((url, (headers or {}).get("authorization")))
        return _Resp(self.body)

    def account_for(self, site=None, url=None):
        return self.acc if site == "gofile.io" else None


def test_premium_token_uses_the_official_api_without_a_browser(monkeypatch):
    gofile._cache.clear()
    monkeypatch.setattr(gofile, "PAGE_FACTORY", lambda: (_ for _ in ()).throw(AssertionError("browser opened")))
    ctx = _ApiCtx({"status": "ok", "data": {"type": "folder", "canAccess": True,
                                             "children": {"f1": file_item(1, "doc.pdf", 500)}}})
    r = gofile.GoFilePlugin().resolve("https://gofile.io/d/abc", ctx)
    assert r.filename == "doc.pdf" and r.headers["cookie"] == "accountToken=premTok"
    assert ctx.calls[0] == ("https://api.gofile.io/contents/abc?pageSize=1000&maxdepth=1", "Bearer premTok")


def test_non_premium_token_falls_back_to_the_website(monkeypatch):
    use({"token": "guest", "data": {"type": "folder", "canAccess": True,
                                    "children": {"f1": file_item(1, "x.bin")}}}, monkeypatch)
    ctx = _ApiCtx({"status": "error-notPremium"})
    r = gofile.GoFilePlugin().resolve("https://gofile.io/d/abc", ctx)
    assert r.headers["cookie"] == "accountToken=guest" and FakePage.opened == ["https://gofile.io/d/abc"]
    with pytest.raises(LoginRequired):
        gofile._cache.clear()
        gofile.GoFilePlugin().resolve("https://gofile.io/d/abc", _ApiCtx({"status": "error-token"}))
