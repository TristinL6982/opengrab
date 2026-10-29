"""Real-browser Cloudflare solver (M6i).

Opens the URL in a real, visible browser so the user (or the browser itself)
can pass a Cloudflare challenge, then polls cookies until a cf_clearance shows
up and stores it in the ClearanceStore. The driver is injectable for tests.
"""
import os
import subprocess
import tempfile
import time
import urllib.parse
from typing import Dict, List, Optional

from opengrab.clearance import Clearance, from_browser_cookies
from opengrab import cdp


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

    def __init__(self, port: int = 9222, user_data_dir: Optional[str] = None, exe: Optional[str] = None, find=None, popen=None, discover=None, transport_factory=None, sleep=time.sleep, attempts: int = 50):
        find = find or cdp.find_browser
        popen = popen or subprocess.Popen
        discover = discover or cdp.discover_ws_url
        transport_factory = transport_factory or cdp.SocketTransport

        exe = exe if exe is not None else find()
        if not exe:
            raise BrowserSolverUnavailable("Microsoft Edge or Google Chrome was not found; install Edge to use the browser solver.")
        
        self.user_data_dir = user_data_dir or os.path.join(tempfile.gettempdir(), "opengrab-cdp-profile")
        
        self._proc = popen(cdp.browser_args(exe, port, self.user_data_dir))
        
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

    def cookies(self) -> List[Dict]:
        return self._client.call("Storage.getCookies").get("cookies", [])

    def user_agent(self) -> str:
        return self._client.call("Browser.getVersion").get("userAgent", "")

    def close(self) -> None:
        try:
            self._client.call("Browser.close", timeout=5.0)
        except cdp.CdpError:
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