import sys
import pytest

from opengrab.browser_solver import (
    BrowserDriver,
    BrowserSolverUnavailable,
    CamoufoxDriver,
    make_stealth_driver,
)


class FakeContext:
    def __init__(self):
        self.items = [{"name": "cf_clearance", "value": "v", "domain": ".example.com"}]

    def cookies(self):
        return self.items


class FakePage:
    def __init__(self):
        self.urls = []
        self.context = FakeContext()

    def goto(self, url):
        self.urls.append(url)

    def evaluate(self, expr):
        if expr == "navigator.userAgent":
            return "FoxUA"
        return None


class FakeBrowser:
    def __init__(self):
        self.page = FakePage()

    def new_page(self):
        return self.page


class FakeCamoufox:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.browser = FakeBrowser()
        self.exited = False

    def __enter__(self):
        return self.browser

    def __exit__(self, *a):
        self.exited = True
        return False


CREATED = []
def make_fake(**kwargs):
    f = FakeCamoufox(**kwargs)
    CREATED.append(f)
    return f


class OtherDriver(BrowserDriver):
    def __init__(self):
        self.name = "other"

    def open(self, url):
        pass

    def cookies(self):
        return []

    def user_agent(self):
        return ""

    def close(self):
        pass


def unavailable():
    raise BrowserSolverUnavailable("no camoufox")


def test_camoufox_driver_visible():
    d = CamoufoxDriver(camoufox_cls=make_fake)
    assert CREATED[-1].kwargs == {"headless": False}


def test_camoufox_driver_open_cookies_ua():
    d = CamoufoxDriver(camoufox_cls=make_fake)
    d.open("https://example.com/x")
    assert CREATED[-1].browser.page.urls == ["https://example.com/x"]
    assert d.cookies()[0]["name"] == "cf_clearance"
    assert d.user_agent() == "FoxUA"


def test_camoufox_driver_close():
    d = CamoufoxDriver(camoufox_cls=make_fake)
    d.close()
    assert CREATED[-1].exited is True


def test_camoufox_missing_raises():
    # Mock the modules to simulate missing camoufox
    original_modules = sys.modules.copy()
    if "camoufox" in sys.modules:
        del sys.modules["camoufox"]
    if "camoufox.sync_api" in sys.modules:
        del sys.modules["camoufox.sync_api"]

    with pytest.raises(BrowserSolverUnavailable, match="pip install camoufox"):
        CamoufoxDriver()

    # Restore modules
    sys.modules.update(original_modules)


def test_stealth_prefers_camoufox():
    d = make_stealth_driver(camoufox_factory=lambda: CamoufoxDriver(camoufox_cls=make_fake), fallback=OtherDriver)
    assert isinstance(d, CamoufoxDriver)


def test_stealth_falls_back():
    d = make_stealth_driver(camoufox_factory=unavailable, fallback=OtherDriver)
    assert d.name == "other"


def test_camoufox_is_browser_driver():
    assert issubclass(CamoufoxDriver, BrowserDriver)