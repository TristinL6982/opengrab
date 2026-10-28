import os
import tempfile
import time
import urllib.parse
from typing import List, Dict, Optional
from dataclasses import dataclass

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Import the necessary functions and classes 
from opengrab.clearance import ClearanceStore, from_browser_cookies, Clearance


class BrowserDriver:
    """Protocol for browser drivers."""
    
    def open(self, url: str) -> None:
        raise NotImplementedError
    
    def cookies(self) -> List[Dict]:
        raise NotImplementedError
    
    def user_agent(self) -> str:
        raise NotImplementedError
        
    def close(self) -> None:
        raise NotImplementedError


class FakeDriver(BrowserDriver):
    """Fake driver for testing."""
    
    def __init__(self, cookie_schedule: List[List[Dict]], user_agent="FakeUA/1.0"):
        self.cookie_schedule = cookie_schedule
        self.user_agent_value = user_agent
        self.opened = []
        self.closed = False
        
    def open(self, url: str) -> None:
        self.opened.append(url)
        
    def cookies(self) -> List[Dict]:
        if not self.cookie_schedule:
            return []
        cookies = self.cookie_schedule.pop(0)
        self.cookie_schedule.append(cookies)  # cycle back to the end
        return cookies
        
    def user_agent(self) -> str:
        return self.user_agent_value
        
    def close(self) -> None:
        self.closed = True


class BrowserSolverUnavailable(Exception):
    """Raised when browser solver cannot be initialized."""
    pass


class PlaywrightDriver(BrowserDriver):
    """Real browser driver using Playwright."""
    
    def __init__(self, channel: str = "msedge", user_data_dir: str = None):
        if not PLAYWRIGHT_AVAILABLE:
            raise BrowserSolverUnavailable(
                "Playwright is not installed. Install with 'pip install playwright' and run 'playwright install msedge'"
            )
        
        self.channel = channel
        self.user_data_dir = user_data_dir or os.path.join(
            tempfile.gettempdir(), "opengrab-browser-profile"
        )
        self.playwright_context = None
        self.page = None
        
    def open(self, url: str) -> None:
        if not self.playwright_context:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=False,
                    channel=self.channel
                )
                self.playwright_context = browser
                self.page = browser.pages[0]
        self.page.goto(url)
        
    def cookies(self) -> List[Dict]:
        return self.playwright_context.cookies()
        
    def user_agent(self) -> str:
        return self.page.evaluate("navigator.userAgent")
        
    def close(self) -> None:
        if self.playwright_context:
            self.playwright_context.close()


def solve_in_browser(
    url: str,
    store: ClearanceStore,
    driver: BrowserDriver,
    timeout: float = 120.0,
    poll: float = 1.0,
    clock=time.monotonic,
    sleep=time.sleep,
    now=None
) -> Optional[Clearance]:
    """Solve Cloudflare clearance in a real browser."""
    host = urllib.parse.urlsplit(url).hostname
    start = clock()
    
    try:
        driver.open(url)
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