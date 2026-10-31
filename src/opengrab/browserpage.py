"""A real, visible browser tab for sites whose file lists only exist after
their own page code has run (GoFile, for example).

OpenGrab opens the page in the installed Edge or Chrome over the DevTools
protocol, waits for the site to load as it would for a visitor, and reads
values from the page (its own API answers, its cookies). Nothing is forged:
the site's own code does the work in a normal browser window, which the user
can see. One tab at a time; the profile lives in the data folder so a site
remembers the browser between uses.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

from . import cdp
from .browser_solver import BrowserSolverUnavailable, CdpDriver

PORT = 9223  # the Cloudflare solver uses 9222; both may run
LOCK_WAIT = 180  # seconds to wait for another page to finish with the browser


class BrowserPageError(Exception):
    pass


class BrowserPage:
    """Context manager: ``with BrowserPage() as page: page.goto(url); page.evaluate(js)``."""

    _busy = threading.Lock()

    def __init__(self, port: int = PORT, driver_factory: Optional[Callable] = None,
                 opener=urllib.request.urlopen, transport_factory=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.port = port
        self._driver_factory = driver_factory or self._default_driver
        self._opener = opener
        self._transport_factory = transport_factory or cdp.SocketTransport
        self._clock, self._sleep = clock, sleep
        self.driver = None
        self.page: Optional[cdp.CdpClient] = None
        self._owns_lock = False

    def _default_driver(self):
        from .settings import app_data_dir
        profile = os.path.join(str(app_data_dir()), "browser-profile")
        return CdpDriver(port=self.port, user_data_dir=profile)

    # -- lifecycle ------------------------------------------------------------
    def __enter__(self) -> "BrowserPage":
        # one browser at a time (one profile, one port); a hung holder must
        # not block every later listing forever
        if not BrowserPage._busy.acquire(timeout=LOCK_WAIT):
            raise BrowserPageError("the built-in browser is busy with another page; try again later")
        self._owns_lock = True
        try:
            self.driver = self._driver_factory()
            self.page = cdp.CdpClient(self._transport_factory(self._page_ws()))
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self.page is not None:
                self.page.close()
        except Exception:
            pass
        try:
            if self.driver is not None:
                self.driver.close()
        except Exception:
            pass
        self.page = self.driver = None
        if self._owns_lock:   # only the instance that took the lock releases it
            self._owns_lock = False
            BrowserPage._busy.release()

    def _page_ws(self) -> str:
        deadline = self._clock() + 15
        while True:
            try:
                with self._opener(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as r:
                    targets = json.loads(r.read().decode("utf-8"))
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
            except (OSError, ValueError):
                pass
            if self._clock() > deadline:
                raise BrowserSolverUnavailable("the browser opened but no tab could be controlled")
            self._sleep(0.3)

    # -- page actions -----------------------------------------------------------
    def goto(self, url: str, timeout: float = 45.0) -> None:
        self.page.call("Page.navigate", {"url": url})
        self.wait_for("document.readyState === 'complete'", timeout)

    def evaluate(self, expression: str, timeout: float = 45.0) -> Any:
        """Run JavaScript in the page; promises are awaited; the value comes back as JSON."""
        res = self.page.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                  "returnByValue": True}, timeout=timeout)
        if res.get("exceptionDetails"):
            d = res["exceptionDetails"]
            msg = (d.get("exception") or {}).get("description") or d.get("text") or "script error"
            raise BrowserPageError(str(msg).splitlines()[0][:300])
        return (res.get("result") or {}).get("value")

    def wait_for(self, expression: str, timeout: float = 30.0, poll: float = 0.5) -> Any:
        deadline = self._clock() + timeout
        while True:
            try:
                value = self.evaluate(expression, timeout=10)
            except (BrowserPageError, cdp.CdpError):
                value = None
            if value:
                return value
            if self._clock() > deadline:
                raise BrowserPageError("the page did not finish loading in time")
            self._sleep(poll)

    def cookies(self, url: str) -> dict:
        res = self.page.call("Network.getCookies", {"urls": [url]})
        return {c["name"]: c["value"] for c in res.get("cookies", []) if "name" in c}

    def user_agent(self) -> str:
        return str(self.evaluate("navigator.userAgent") or "")
