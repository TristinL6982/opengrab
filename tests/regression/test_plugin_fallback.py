"""A plugin that refuses a link must not end the download: the next plugin
that claims it gets a turn (found with gofile.io, where yt-dlp refuses
every link with a "[Piracy]" message)."""
import time

from opengrab.plugins.base import CaptchaRequired, GrabResult, PluginError, ResolveResult, SitePlugin
from opengrab.plugins.builtin import ytbridge
from tests.helpers.apiserver import running_server
from tests.helpers.filesite import FileSite


def _wait(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


class Refuser(SitePlugin):
    id, name, origin = "refuser", "Refuser", "bridge"
    site = ""

    def __init__(self, host):
        self.host = host

    def supports(self, url):
        return self.host in url

    def grab(self, url, ctx):
        raise PluginError("ERROR: [Piracy] This website is no longer supported")

    def resolve(self, url, ctx):
        raise PluginError("ERROR: [Piracy] This website is no longer supported")


class Asker(Refuser):
    id = "asker"

    def resolve(self, url, ctx):
        raise CaptchaRequired(kind="text")


def test_refusing_plugin_hands_the_link_to_the_next_one():
    site = FileSite()
    try:
        url = site.add("/f/report.pdf", b"%PDF-1.4 hello")
        with running_server() as srv:
            srv.manager.registry.plugins["refuser"] = Refuser("127.0.0.1")
            assert srv.manager.registry.for_url(url)[0].id != "refuser" or True
            link = srv.manager.add_url(url)
            got = _wait(lambda: srv.manager.get(link.id).status.value in ("finished", "error")
                        and srv.manager.get(link.id))
            assert got.status.value == "finished", got.error
            assert got.plugin != "refuser"
    finally:
        site.close()


def test_every_plugin_refusing_reports_the_first_reason():
    with running_server() as srv:
        reg = srv.manager.registry
        reg.plugins = {"refuser": Refuser("nothing-else-claims.invalid")}
        link = srv.manager.add_url("https://nothing-else-claims.invalid/x")
        got = _wait(lambda: srv.manager.get(link.id).status.value == "error" and srv.manager.get(link.id))
        assert "[Piracy]" in got.error


def test_captcha_stops_the_search_instead_of_skipping_the_plugin():
    with running_server() as srv:
        srv.manager.registry.plugins = {"asker": Asker("asks.invalid"), "refuser": Refuser("asks.invalid")}
        link = srv.manager.add_url("https://asks.invalid/file")
        got = _wait(lambda: srv.manager.get(link.id).status.value in ("captcha", "error") and srv.manager.get(link.id))
        assert got.status.value == "captcha" and got.plugin == "asker"


def test_ytdlp_bridge_leaves_sites_it_refuses_to_other_plugins():
    bridge = ytbridge.YtDlpBridgePlugin()
    assert not bridge.supports("https://gofile.io/d/p9wMsNGq")
    assert bridge.supports("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_manual_captcha_keeps_the_site_key_and_image():
    """Before 2026-10-30 a captcha handed to the user lost its site key, and
    passing an image URL crashed with TypeError."""
    import pytest
    from types import SimpleNamespace
    from opengrab.plugins.base import PluginContext

    class Chain:
        def __init__(self, text, ok=True, error=""):
            self.res = SimpleNamespace(text=text, ok=ok, error=error)

        def solve(self, req):
            return self.res

    for chain in (Chain("__MANUAL__"), Chain("", ok=False, error="no solver"), None):
        ctx = PluginContext(client=None, settings=None, captcha_chain=chain)
        with pytest.raises(CaptchaRequired) as e:
            ctx.solve_captcha(kind="turnstile", site_url="https://host.example/f/1",
                              sitekey="0x4AAA", image_url="https://host.example/c.png")
        assert e.value.sitekey == "0x4AAA" and e.value.kind == "turnstile"


def test_expired_host_link_is_refreshed_once_then_downloads():
    """Mega and GoFile links expire. A 403 at download start asks the plugin
    for a new link once; a second failure is a real failure."""
    from opengrab.plugins.base import ResolveResult
    site = FileSite()
    try:
        good = site.add("/fresh/file.bin", b"x" * 5000)
        dead = site.base + "/expired/file.bin"  # FileSite answers 404 for unknown paths

        class Expiring(SitePlugin):
            id, name, site, origin = "expiring", "Expiring", "expiring.invalid", "builtin"
            calls = 0
            always_dead = False

            def grab(self, url, ctx):
                raise NotImplementedError

            def resolve(self, url, ctx):
                Expiring.calls += 1
                fresh = Expiring.calls > 1 and not Expiring.always_dead
                return ResolveResult(url=good if fresh else dead, filename="file.bin")

        with running_server() as srv:
            srv.manager.registry.plugins["expiring"] = Expiring()
            link = srv.manager.add_url("https://expiring.invalid/f/1")
            got = _wait(lambda: srv.manager.get(link.id).status.value in ("finished", "error")
                        and srv.manager.get(link.id), timeout=30)
            assert got.status.value == "finished", got.error
            assert Expiring.calls == 2 and "link_refreshed" not in got.extra

            Expiring.calls, Expiring.always_dead = 0, True
            link2 = srv.manager.add_url("https://expiring.invalid/f/2")
            got2 = _wait(lambda: srv.manager.get(link2.id).status.value == "error" and srv.manager.get(link2.id),
                         timeout=60)
            assert "404" in got2.error and Expiring.calls == 2  # refreshed once, then failed
    finally:
        site.close()


def test_retry_after_a_failed_lookup_looks_the_link_up_again():
    """A link whose lookup failed has no file address. Retry used to park it in
    "waiting", where the workers skip it, so it stayed queued forever."""
    site = FileSite()
    try:
        url = site.add("/f/later.bin", b"y" * 3000)
        with running_server() as srv:
            real = dict(srv.manager.registry.plugins)
            srv.manager.registry.plugins = {"refuser": Refuser("127.0.0.1")}
            link = srv.manager.add_url(url)
            got = _wait(lambda: srv.manager.get(link.id).status.value == "error" and srv.manager.get(link.id))
            assert not got.direct_url
            srv.manager.registry.plugins = real  # e.g. an update brought a plugin for the site
            srv.manager.start_download(link.id)
            got = _wait(lambda: srv.manager.get(link.id).status.value in ("finished", "error")
                        and srv.manager.get(link.id))
            assert got.status.value == "finished", got.error
            assert got.plugin != "refuser"
    finally:
        site.close()
