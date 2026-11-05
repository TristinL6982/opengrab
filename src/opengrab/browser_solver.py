"""Real-browser Cloudflare solver (M6i).

Opens the URL in a real, visible browser so the user (or the browser itself)
can pass a Cloudflare challenge, then polls cookies until a cf_clearance shows
up and stores it in the ClearanceStore. The driver is injectable for tests.
"""
import os
import subprocess
import tempfile
import time
import logging
import urllib.parse
from typing import Dict, List, Optional

from opengrab.clearance import Clearance, from_browser_cookies
from opengrab import cdp


log = logging.getLogger(__name__)


class BrowserSolverUnavailable(Exception):
    """Raised when the real-browser solver cannot run (e.g. playwright missing)."""


class BrowserDriver:
    """Minimal browser interface used by solve_in_browser."""

    def open(self, url: str) -> None:
        raise NotImplementedError

    def cookies(self) -> List[Dict]:
        raise NotImplementedError

    def user_agent(self) -> str:
        raise NotImplementedError

    def open_page(self, url: str) -> str:
        """Open url in a new tab; return a handle for evaluate()."""
        raise BrowserSolverUnavailable("this browser cannot show a captcha on the site's own page")

    def evaluate(self, session: str, expression: str):
        raise BrowserSolverUnavailable("this browser cannot show a captcha on the site's own page")

    def show(self) -> None:
        """Bring the window forward so the user can click; no-op if it is already visible."""

    def close(self) -> None:
        raise NotImplementedError


class FakeDriver(BrowserDriver):
    """Test driver: each cookies() call returns the next list in the schedule,
    repeating the last one once the schedule is exhausted."""

    def __init__(self, cookie_schedule: List[List[Dict]], user_agent: str = "FakeUA/1.0"):
        self._schedule = list(cookie_schedule)
        self._index = 0
        self._ua = user_agent
        self.opened: List[str] = []
        self.closed = False

    def open(self, url: str) -> None:
        self.opened.append(url)

    def cookies(self) -> List[Dict]:
        if not self._schedule:
            return []
        i = min(self._index, len(self._schedule) - 1)
        self._index += 1
        return list(self._schedule[i])

    def user_agent(self) -> str:
        return self._ua

    def close(self) -> None:
        self.closed = True


class PlaywrightDriver(BrowserDriver):
    """Real browser via Playwright: a visible persistent context (Edge by default)."""

    def __init__(self, channel: str = "msedge", user_data_dir: Optional[str] = None):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserSolverUnavailable(
                "The real-browser solver needs Playwright: run "
                "'pip install playwright' (Edge/Chrome must be installed, or run "
                "'playwright install chromium')."
            ) from e
        self.channel = channel
        self.user_data_dir = user_data_dir or os.path.join(
            tempfile.gettempdir(), "opengrab-browser-profile"
        )
        self._pw = sync_playwright().start()
        try:
            self._context = self._pw.chromium.launch_persistent_context(
                self.user_data_dir, headless=False, channel=channel
            )
        except Exception:
            self._pw.stop()
            raise
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

    def open(self, url: str) -> None:
        self._page.goto(url)

    def cookies(self) -> List[Dict]:
        return self._context.cookies()

    def user_agent(self) -> str:
        return self._page.evaluate("navigator.userAgent")

    def close(self) -> None:
        try:
            self._context.close()
        finally:
            self._pw.stop()


class CdpDriver(BrowserDriver):
    """Real visible Edge/Chrome driven over CDP (no Playwright, never headless)."""

    def __init__(self, port: int = 9222, user_data_dir: Optional[str] = None, exe: Optional[str] = None, find=None, popen=None, discover=None, transport_factory=None, sleep=time.sleep, attempts: int = 50, hidden: bool = False):
        find = find or cdp.find_browser
        popen = popen or subprocess.Popen
        discover = discover or cdp.discover_ws_url
        transport_factory = transport_factory or cdp.SocketTransport

        exe = exe if exe is not None else find()
        if not exe:
            raise BrowserSolverUnavailable("Microsoft Edge or Google Chrome was not found; install Edge to use the browser solver.")
        
        self.user_data_dir = user_data_dir or os.path.join(tempfile.gettempdir(), "opengrab-cdp-profile")
        
        self.hidden = hidden
        self._target = ""
        self._proc = popen(cdp.browser_args(exe, port, self.user_data_dir, hidden=hidden))
        
        ws = ""
        last = None
        for _ in range(attempts):
            try:
                ws = discover(port)
                break
            except cdp.CdpError as e:
                last = e
                sleep(0.2)
                
        if not ws:
            self._proc.terminate()
            raise BrowserSolverUnavailable("browser devtools did not start: " + str(last))
            
        try:
            self._client = cdp.CdpClient(transport_factory(ws))
        except cdp.CdpError as e:
            self._proc.terminate()
            raise BrowserSolverUnavailable("cannot attach to browser: " + str(e))

    def open(self, url: str) -> None:
        self._client.call("Target.createTarget", {"url": url})

    def open_page(self, url: str) -> str:
        """Open url in a new tab and return a session id for evaluate()."""
        target = self._client.call("Target.createTarget", {"url": url}).get("targetId", "")
        self._target = target
        self._session = self._client.call("Target.attachToTarget",
                                          {"targetId": target, "flatten": True}).get("sessionId", "")
        return self._session

    def show(self) -> None:
        """Move a hidden (off-screen) window onto the screen and in front, so the user can click."""
        if not self.hidden or not self._target:
            return
        try:
            win = self._client.call("Browser.getWindowForTarget", {"targetId": self._target})
            self._client.call("Browser.setWindowBounds", {
                "windowId": win.get("windowId"),
                "bounds": {"windowState": "normal", "left": 120, "top": 80, "width": 560, "height": 720}})
            self._client.call("Target.activateTarget", {"targetId": self._target})
            if self._session:
                self._client.call("Page.bringToFront", session_id=self._session)
        except cdp.CdpError as e:
            log.info("could not bring the browser window forward: %s", e)
        self.hidden = False

    def evaluate(self, session: str, expression: str):
        res = self._client.call("Runtime.evaluate", {"expression": expression, "returnByValue": True},
                                session_id=session)
        return (res.get("result") or {}).get("value")

    def cookies(self) -> List[Dict]:
        return self._client.call("Storage.getCookies").get("cookies", [])

    def user_agent(self) -> str:
        return self._client.call("Browser.getVersion").get("userAgent", "")

    def close(self) -> None:
        try:
            self._client.call("Browser.close", timeout=5.0)
        except (cdp.CdpError, OSError):
            pass
            
        try:
            self._client.close()
        except (cdp.CdpError, OSError):
            pass
            
        if self._proc.poll() is None:
            self._proc.terminate()


def solve_in_browser(
    url: str,
    store,
    driver: BrowserDriver,
    timeout: float = 120.0,
    poll: float = 1.0,
    clock=time.monotonic,
    sleep=time.sleep,
    now=None,
) -> Optional[Clearance]:
    """Open url in driver and poll until a cf_clearance appears or timeout passes.

    Stores and returns the Clearance, or returns None on timeout. Always closes
    the driver.
    """
    host = urllib.parse.urlsplit(url).hostname
    try:
        driver.open(url)
        start = clock()
        while True:
            c = from_browser_cookies(host, driver.cookies(), driver.user_agent(), now=now)
            if c is not None:
                store.put(c)
                return c
            if clock() - start >= timeout:
                return None
            sleep(poll)
    finally:
        driver.close()


WIDGETS = {"turnstile": ("cf-turnstile", "https://challenges.cloudflare.com/turnstile/v0/api.js"),
           "hcaptcha": ("h-captcha", "https://js.hcaptcha.com/1/api.js"),
           "recaptcha": ("g-recaptcha", "https://www.google.com/recaptcha/api.js")}


def widget_page(kind: str, sitekey: str, host: str) -> str:
    """A page that shows one captcha widget and keeps its answer in window.__ogToken."""
    import html as _html
    import re as _re
    if kind not in WIDGETS:
        raise ValueError(f"unsupported captcha kind: {kind}")
    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sitekey or ""):
        raise ValueError("invalid sitekey")
    cls, script = WIDGETS[kind]
    h = _html.escape(host)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>OpenGrab: check for {h}</title></head>"
            "<body style='font-family:Segoe UI,sans-serif;padding:24px'>"
            f"<p>OpenGrab needs this check to download from {h}. It often passes by itself; "
            "if it asks, complete it here.</p>"
            f"<div class='{cls}' data-sitekey='{sitekey}' data-callback='__ogDone'></div>"
            "<script>window.__ogDone=function(t){window.__ogToken=t;"
            "document.body.insertAdjacentHTML('beforeend','<p>Done. OpenGrab is continuing; "
            "this window closes by itself.</p>')}</script>"
            f"<script src='{script}' async defer></script></body></html>")


def solve_widget_in_browser(
    page_url: str,
    kind: str,
    sitekey: str,
    driver: BrowserDriver,
    timeout: float = 120.0,
    poll: float = 1.0,
    clock=time.monotonic,
    sleep=time.sleep,
    show_after: float = 8.0,
) -> Optional[str]:
    """Show a site's captcha widget on the site's own address and return its token.

    Widgets such as Turnstile only work on the hostnames their site allows,
    so the widget is drawn into a page of that site (its robots.txt, replaced
    in the browser): the site's own pages are not needed, and a page that
    depends on the download's cookies (DataNodes' /download) is never shown
    out of context. A driver started hidden is brought forward (driver.show())
    once, if no token has arrived after show_after seconds: the check needs a
    click. Returns None on timeout. Always closes the driver."""
    import json as _json
    p = urllib.parse.urlsplit(page_url)
    origin = f"{p.scheme}://{p.netloc}"
    html = widget_page(kind, sitekey, p.hostname or "")
    try:
        session = driver.open_page(origin + "/robots.txt")
        start = clock()
        # a new tab is about:blank (already "complete") until the site's page arrives
        ready = f"location.origin === {_json.dumps(origin)} && document.readyState !== 'loading'"
        while not driver.evaluate(session, ready):
            if clock() - start >= timeout:
                return None
            sleep(poll / 4)
        driver.evaluate(session, f"document.open();document.write({_json.dumps(html)});document.close();true")
        shown = False
        while True:
            token = driver.evaluate(session, "window.__ogToken || ''")
            if token:
                return str(token)
            waited = clock() - start
            if waited >= timeout:
                return None
            if not shown and waited >= show_after:
                shown = True
                driver.show()
            sleep(poll)
    finally:
        driver.close()


# explicit-render API of each widget: (script url with ?render=explicit&onload=, JS global, render call)
EXPLICIT = {"turnstile": ("https://challenges.cloudflare.com/turnstile/v0/api.js", "turnstile"),
            "hcaptcha": ("https://js.hcaptcha.com/1/api.js", "hcaptcha"),
            "recaptcha": ("https://www.google.com/recaptcha/api.js", "grecaptcha")}
MAX_WIDGETS = 200


def widgets_page(kind: str, sitekey: str, host: str, count: int) -> str:
    """A page that renders `count` widgets of one site key (explicit render API).
    Each answer is appended to window.__ogTokens; the heading counts progress."""
    import html as _html
    import json as _json
    import re as _re
    if kind not in EXPLICIT:
        raise ValueError(f"unsupported captcha kind: {kind}")
    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sitekey or ""):
        raise ValueError("invalid sitekey")
    count = int(count)
    if not 1 <= count <= MAX_WIDGETS:
        raise ValueError("invalid widget count")
    script, api = EXPLICIT[kind]
    h = _html.escape(host)
    js_host = _json.dumps(host).replace("<", "\\u003c")
    cells = "".join(f"<div class='ogw' id='ogw{i}' style='display:inline-block;margin:4px;min-width:300px'></div>"
                    for i in range(count))
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>OpenGrab: 0 of {count} checks done for {h}</title></head>"
            "<body style='font-family:Segoe UI,sans-serif;padding:24px'>"
            f"<h3 id='ogp'>OpenGrab: 0 of {count} checks done for {h}</h3>"
            "<p>These checks often pass by themselves; if one asks, complete it here.</p>"
            f"{cells}"
            "<script>window.__ogTokens=[];"
            f"window.__ogHost={js_host};window.__ogN={count};"
            "window.__ogProgress=function(){var t='OpenGrab: '+window.__ogTokens.length+' of '+window.__ogN"
            "+' checks done for '+window.__ogHost;document.title=t;document.getElementById('ogp').textContent=t};"
            "window.__ogReady=function(){for(var i=0;i<window.__ogN;i++){(function(i){setTimeout(function(){"
            f"{api}.render(document.getElementById('ogw'+i),{{sitekey:'{sitekey}',callback:function(t){{window.__ogTokens.push(t);"
            "window.__ogProgress()}})},i*100)})(i)}};</script>"
            f"<script src='{script}?render=explicit&onload=__ogReady' async defer></script></body></html>")


def solve_widgets_in_browser(
    page_url: str,
    kind: str,
    sitekey: str,
    count: int,
    driver: BrowserDriver,
    on_token,
    timeout: float = 120.0,
    poll: float = 0.5,
    clock=time.monotonic,
    sleep=time.sleep,
    show_after: float = 8.0,
) -> int:
    """Render `count` widgets on ONE page of the site and hand every token to
    on_token(token) as it arrives (tokens work once and expire after about five
    minutes, so each is used at once). A driver started hidden is brought
    forward (driver.show()) once if not all tokens are in after show_after
    seconds. Returns how many tokens were delivered. Always closes the driver."""
    import json as _json
    p = urllib.parse.urlsplit(page_url)
    origin = f"{p.scheme}://{p.netloc}"
    html = widgets_page(kind, sitekey, p.hostname or "", count)   # validates kind, sitekey and count
    delivered = 0
    try:
        session = driver.open_page(origin + "/robots.txt")
        start = clock()
        ready = f"location.origin === {_json.dumps(origin)} && document.readyState !== 'loading'"
        while not driver.evaluate(session, ready):
            if clock() - start >= timeout:
                return 0
            sleep(poll / 4)
        driver.evaluate(session, f"document.open();document.write({_json.dumps(html)});document.close();true")
        shown = False
        while delivered < count:
            raw = driver.evaluate(session, f"JSON.stringify((window.__ogTokens||[]).slice({delivered}))")
            try:
                fresh = _json.loads(raw) if raw else []
            except (TypeError, ValueError):
                fresh = []
            for token in fresh[:count - delivered]:
                delivered += 1
                try:
                    on_token(str(token))
                except Exception:  # noqa: BLE001 - one bad consumer must not stop the others
                    log.exception("a captcha token consumer failed")
            if delivered >= count:
                break
            waited = clock() - start
            if waited >= timeout:
                break
            if not shown and waited >= show_after:
                shown = True
                driver.show()
            sleep(poll)
        return delivered
    finally:
        driver.close()


def make_driver(cdp_factory=None, playwright_factory=None, hidden: bool = False) -> BrowserDriver:
    """Return a CdpDriver (visible Edge over CDP); fall back to PlaywrightDriver if CDP is unavailable."""
    cdp_factory = cdp_factory or CdpDriver
    playwright_factory = playwright_factory or PlaywrightDriver
    try:
        return cdp_factory(hidden=True) if hidden else cdp_factory()
    except BrowserSolverUnavailable as e1:
        first = str(e1)
    try:
        return playwright_factory()
    except BrowserSolverUnavailable as e2:
        raise BrowserSolverUnavailable("no browser solver available: " + first + "; " + str(e2))


def solve_clearance(url: str, store, make=None, **kwargs) -> Optional[Clearance]:
    """Solve a Cloudflare challenge for url in the default browser driver."""
    make = make or make_driver
    return solve_in_browser(url, store, make(), **kwargs)


class CamoufoxDriver(BrowserDriver):
    """Optional stealth browser (Camoufox, hardened Firefox), visible window. Opt-in; ~300 MB download."""
    def __init__(self, camoufox_cls=None):
        if camoufox_cls is None:
            try:
                from camoufox.sync_api import Camoufox as camoufox_cls
            except ImportError as e:
                raise BrowserSolverUnavailable("Camoufox is optional: run 'pip install camoufox' then 'python -m camoufox fetch' (about 300 MB).") from e
        self._cm = camoufox_cls(headless=False)
        self._browser = self._cm.__enter__()
        self._page = self._browser.new_page()

    def open(self, url):
        self._page.goto(url)

    def cookies(self):
        return self._page.context.cookies()

    def user_agent(self):
        return self._page.evaluate("navigator.userAgent")

    def close(self):
        self._cm.__exit__(None, None, None)


def make_stealth_driver(camoufox_factory=None, fallback=None) -> BrowserDriver:
    """Return a CamoufoxDriver; fall back to the default driver (make_driver) if Camoufox is unavailable."""
    camoufox_factory = camoufox_factory or CamoufoxDriver
    fallback = fallback or make_driver
    try:
        return camoufox_factory()
    except BrowserSolverUnavailable:
        return fallback()