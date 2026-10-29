import pytest
from opengrab.browser_solver import (
    BrowserDriver,
    BrowserSolverUnavailable,
    PlaywrightDriver,
    CdpDriver,
    solve_in_browser,
    make_driver,
    solve_clearance,
)
from opengrab.clearance import ClearanceStore


def test_prefers_cdp():
    """Test that make_driver prefers CdpDriver over PlaywrightDriver."""
    class FakeDriver(BrowserDriver):
        def __init__(self, name, cookies=None):
            self.name = name
            self._cookies = cookies or []
            self.opened = []
            self.closed = False

        def open(self, url):
            self.opened.append(url)

        def cookies(self):
            return self._cookies

        def user_agent(self):
            return "UA"

        def close(self):
            self.closed = True

    d = make_driver(cdp_factory=lambda: FakeDriver("cdp"), playwright_factory=lambda: FakeDriver("pw"))
    assert d.name == "cdp"


def test_falls_back_to_playwright():
    """Test that make_driver falls back to PlaywrightDriver when CDP is unavailable."""
    class FakeDriver(BrowserDriver):
        def __init__(self, name, cookies=None):
            self.name = name
            self._cookies = cookies or []
            self.opened = []
            self.closed = False

        def open(self, url):
            self.opened.append(url)

        def cookies(self):
            return self._cookies

        def user_agent(self):
            return "UA"

        def close(self):
            self.closed = True

    # Mock the CdpDriver to raise BrowserSolverUnavailable
    def unavailable_cdp():
        raise BrowserSolverUnavailable("no edge")

    d = make_driver(cdp_factory=unavailable_cdp, playwright_factory=lambda: FakeDriver("pw"))
    assert d.name == "pw"


def test_both_unavailable():
    """Test that make_driver raises BrowserSolverUnavailable when both drivers are unavailable."""
    def unavailable_cdp():
        raise BrowserSolverUnavailable("no edge")

    def unavailable_pw():
        raise BrowserSolverUnavailable("no playwright")

    with pytest.raises(BrowserSolverUnavailable, match="no browser solver available: no edge; no playwright"):
        make_driver(cdp_factory=unavailable_cdp, playwright_factory=unavailable_pw)


def test_default_factories_are_real_classes():
    """Test that default factories are real classes."""
    import inspect
    assert inspect.signature(make_driver).parameters["cdp_factory"].default is None
    assert issubclass(CdpDriver, BrowserDriver)
    assert issubclass(PlaywrightDriver, BrowserDriver)


def test_solve_clearance_uses_driver():
    """Test that solve_clearance uses the default driver."""
    class FakeDriver(BrowserDriver):
        def __init__(self, name, cookies=None):
            self.name = name
            self._cookies = cookies or []
            self.opened = []
            self.closed = False

        def open(self, url):
            self.opened.append(url)

        def cookies(self):
            return self._cookies

        def user_agent(self):
            return "UA"

        def close(self):
            self.closed = True

    # Create a proper cf_clearance cookie format like the real code expects
    clearance_cookie = {
        'name': 'cf_clearance',
        'value': 'v',
        'domain': '.example.com',
        'expires': 4102444800
    }
    
    d = FakeDriver("cdp", cookies=[clearance_cookie])
    store = ClearanceStore(None)
    c = solve_clearance("https://example.com/x", store, make=lambda: d, timeout=0, sleep=lambda s: None)
    assert d.opened == ["https://example.com/x"]
    assert d.closed is True
    assert c is not None
    assert len(store._clearances) == 1


def test_solve_clearance_timeout():
    """Test that solve_clearance handles timeout correctly."""
    class FakeDriver(BrowserDriver):
        def __init__(self, name, cookies=None):
            self.name = name
            self._cookies = cookies or []
            self.opened = []
            self.closed = False

        def open(self, url):
            self.opened.append(url)

        def cookies(self):
            return self._cookies

        def user_agent(self):
            return "UA"

        def close(self):
            self.closed = True

    d = FakeDriver("cdp")
    store = ClearanceStore(None)
    c = solve_clearance("https://example.com/x", store, make=lambda: d, timeout=0, sleep=lambda s: None)
    assert c is None
    assert len(store._clearances) == 0
    assert d.closed is True