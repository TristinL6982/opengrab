import os
import tempfile
import time
from unittest.mock import patch
import pytest

# Import the functions we want to test
from opengrab.browser_solver import (
    BrowserDriver,
    FakeDriver,
    PlaywrightDriver,
    solve_in_browser,
    BrowserSolverUnavailable
)
from opengrab.clearance import ClearanceStore, from_browser_cookies, Clearance


def test_clearance_on_third_poll():
    """Test that clearance is returned on the third poll."""
    schedule = [
        [], 
        [{"name":"other","value":"x","domain":".example.com","expires":-1}], 
        [{"name":"cf_clearance","value":"abc","domain":".example.com","expires":4102444800}]
    ]
    driver = FakeDriver(schedule)
    store_path = os.path.join(tempfile.mkdtemp(), "cl.json")
    store = ClearanceStore(store_path)
    
    def sleep(s):
        pass
    
    result = solve_in_browser(
        "https://example.com/file.zip", 
        store, 
        driver,
        sleep=sleep
    )
    
    assert result is not None
    assert result.cookies["cf_clearance"] == "abc"
    assert store.get("example.com") is not None
    assert driver.opened == ["https://example.com/file.zip"]
    assert driver.closed is True


def test_timeout():
    """Test that the function times out correctly."""
    schedule = [[]]
    driver = FakeDriver(schedule)
    store_path = os.path.join(tempfile.mkdtemp(), "cl.json")
    store = ClearanceStore(store_path)
    
    it = iter([0, 0, 5, 10, 200])
    def clock():
        return next(it)
        
    result = solve_in_browser(
        "https://example.com/file.zip", 
        store, 
        driver,
        timeout=100,
        clock=clock
    )
    
    assert result is None
    assert driver.closed is True
    # Check if store is empty (no entries were added)
    assert len(store._clearances) == 0


def test_driver_close_on_cookies_error():
    """Test that driver.close() is called when cookies() raises."""
    class ErrorDriver(FakeDriver):
        def cookies(self):
            raise RuntimeError("Cookies error")
    
    schedule = [[]]
    driver = ErrorDriver(schedule)
    store_path = os.path.join(tempfile.mkdtemp(), "cl.json")
    store = ClearanceStore(store_path)
    
    with pytest.raises(RuntimeError):
        solve_in_browser(
            "https://example.com/file.zip", 
            store, 
            driver
        )
    
    assert driver.closed is True


def test_user_agent_passes_through():
    """Test that the user agent passes through correctly."""
    schedule = [
        [{"name":"cf_clearance","value":"abc","domain":".example.com","expires":4102444800}]
    ]
    driver = FakeDriver(schedule, user_agent="FakeUA/1.0")
    store_path = os.path.join(tempfile.mkdtemp(), "cl.json")
    store = ClearanceStore(store_path)
    
    def sleep(s):
        pass
    
    result = solve_in_browser(
        "https://example.com/file.zip", 
        store, 
        driver,
        sleep=sleep
    )
    
    assert result.user_agent == "FakeUA/1.0"


def test_module_imports():
    """Test that the module can be imported correctly."""
    import opengrab.browser_solver
    
    # Check that classes exist
    assert hasattr(opengrab.browser_solver, 'BrowserDriver')
    assert hasattr(opengrab.browser_solver, 'PlaywrightDriver')
    
    # Check inheritance
    assert issubclass(PlaywrightDriver, BrowserDriver)
    
    # Check exception exists
    assert hasattr(opengrab.browser_solver, 'BrowserSolverUnavailable')
    assert issubclass(BrowserSolverUnavailable, Exception)

def test_fake_driver_repeats_last_cookie_list():
    """FakeDriver repeats the last schedule entry once exhausted (no cycling)."""
    a = [{"name": "a", "value": "1", "domain": ".example.com", "expires": -1}]
    b = [{"name": "b", "value": "2", "domain": ".example.com", "expires": -1}]
    driver = FakeDriver([a, b])
    assert driver.cookies() == a
    assert driver.cookies() == b
    assert driver.cookies() == b
    assert driver.cookies() == b


def test_playwright_driver_missing_raises_unavailable():
    """Without playwright, PlaywrightDriver raises BrowserSolverUnavailable."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(BrowserSolverUnavailable):
            PlaywrightDriver()