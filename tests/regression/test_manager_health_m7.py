import tempfile
import os
from pathlib import Path
from opengrab.engine.manager import DownloadManager
from opengrab.settings import Settings
from opengrab.models import Link
from opengrab.plugins.base import PluginContext, CaptchaRequired, LoginRequired, PaywallRequired, PluginError
from opengrab import models

def test_resolve_returns_result():
    """Test that resolve returns a result with a url -> mgr.registry.health_for("fake_m7d")["ok"] == 1 and ["fail"] == 0."""
    # Create a temporary directory for downloads
    downloads = Path(tempfile.mkdtemp(prefix="opengrab-m7d-"))
    
    # Set up the manager with minimal settings 
    settings = Settings(download_dir=str(downloads), max_parallel=1)
    mgr = DownloadManager(settings)
    
    # Create a fake plugin for testing
    class FakePlugin:
        def __init__(self):
            self.id = "fake_m7d"
            
        def resolve(self, url, ctx):
            # Simulate a successful resolution with direct URL
            return models.LinkResolveResult(url="https://example.com/file.zip")
    
    # Create test link
    link = Link(url="https://example.com/test")
    
    # Get the context
    ctx = mgr._ctx()
    
    # Call _resolve directly with our fake plugin
    mgr._resolve(link, FakePlugin(), ctx)
    
    # Verify health stats 
    stats = mgr.registry.health_for("fake_m7d")
    assert stats["ok"] == 1
    assert stats["fail"] == 0

def test_resolve_raises_plugin_error():
    """Test that resolve raises PluginError("boom") -> pytest.raises(PluginError); health fail == 1; last_error == "boom".""" 
    # Create a temporary directory for downloads
    downloads = Path(tempfile.mkdtemp(prefix="opengrab-m7d-"))
    
    # Set up the manager with minimal settings 
    settings = Settings(download_dir=str(downloads), max_parallel=1)
    mgr = DownloadManager(settings)
    
    # Create a fake plugin for testing
    class FakePlugin:
        def __init__(self):
            self.id = "fake_m7d"
            
        def resolve(self, url, ctx):
            raise PluginError("boom")
    
    # Create test link
    link = Link(url="https://example.com/test")
    
    # Get the context
    ctx = mgr._ctx()
    
    # Call _resolve directly with our fake plugin - should raise PluginError
    try:
        mgr._resolve(link, FakePlugin(), ctx)
        assert False, "Should have raised PluginError"
    except PluginError as e:
        assert str(e) == "boom"
        
    # Verify health stats 
    stats = mgr.registry.health_for("fake_m7d")
    assert stats["fail"] == 1
    assert stats["last_error"] == "boom"

def test_resolve_returns_no_url_and_no_children():
    """Test that resolve returns no url and no children -> pytest.raises(PluginError); fail == 1; last_error == "no direct url and no children"."""
    # Create a temporary directory for downloads
    downloads = Path(tempfile.mkdtemp(prefix="opengrab-m7d-"))
    
    # Set up the manager with minimal settings 
    settings = Settings(download_dir=str(downloads), max_parallel=1)
    mgr = DownloadManager(settings)
    
    # Create a fake plugin for testing
    class FakePlugin:
        def __init__(self):
            self.id = "fake_m7d"
            
        def resolve(self, url, ctx):
            # Return empty result - no direct_url or children
            return models.LinkResolveResult()
    
    # Create test link with no children to start
    link = Link(url="https://example.com/test")
    
    # Get the context
    ctx = mgr._ctx()
    
    # Call _resolve directly with our fake plugin - should raise PluginError 
    try:
        mgr._resolve(link, FakePlugin(), ctx)
        assert False, "Should have raised PluginError"
    except PluginError as e:
        expected_msg = "plugin 'fake_m7d' produced no direct url and no children"  
        assert str(e) == expected_msg
        
    # Verify health stats 
    stats = mgr.registry.health_for("fake_m7d")
    assert stats["fail"] == 1
    assert stats["last_error"] == "no direct url and no children"

def test_resolve_raises_captcha_required():
    """Test that resolve raises CaptchaRequired -> pytest.raises(CaptchaRequired); health_for("fake_m7d") == {"ok": 0, "fail": 0, "last_error": None, "last_used": None}."""
    # Create a temporary directory for downloads
    downloads = Path(tempfile.mkdtemp(prefix="opengrab-m7d-"))
    
    # Set up the manager with minimal settings 
    settings = Settings(download_dir=str(downloads), max_parallel=1)
    mgr = DownloadManager(settings)
    
    # Create a fake plugin for testing
    class FakePlugin:
        def __init__(self):
            self.id = "fake_m7d"
            
        def resolve(self, url, ctx):
            raise CaptchaRequired(image_b64="fake_image_data", site_url="https://example.com")

    # Create test link
    link = Link(url="https://example.com/test")
    
    # Get the context
    ctx = mgr._ctx()
    
    # Call _resolve directly with our fake plugin - should raise CaptchaRequired
    try:
        mgr._resolve(link, FakePlugin(), ctx)
        assert False, "Should have raised CaptchaRequired"
    except CaptchaRequired as e:
        assert e.image_b64 == "fake_image_data" 
        assert e.site_url == "https://example.com"
        
    # Verify health stats - should be unchanged
    stats = mgr.registry.health_for("fake_m7d")
    assert stats["ok"] == 0
    assert stats["fail"] == 0
    assert stats["last_error"] is None

def test_link_direct_url_already_set():
    """Test that _resolve records nothing when link.direct_url already set."""
    # Create a temporary directory for downloads
    downloads = Path(tempfile.mkdtemp(prefix="opengrab-m7d-"))
    
    # Set up the manager with minimal settings 
    settings = Settings(download_dir=str(downloads), max_parallel=1)
    mgr = DownloadManager(settings)
    
    # Create a fake plugin for testing (should never be called)
    class FakePlugin:
        def __init__(self):
            self.id = "fake_m7d"
            
        def resolve(self, url, ctx):
            raise Exception("Should not be called")
    
    # Create test link with direct_url already set
    link = Link(url="https://example.com/test", direct_url="https://example.com/file.zip")
    
    # Get the context
    ctx = mgr._ctx()
    
    # Call _resolve directly - should succeed without recording anything
    mgr._resolve(link, FakePlugin(), ctx)
    
    # Verify health stats are unchanged for a new plugin ID
    stats = mgr.registry.health_for("fake_m7d")
    assert stats["ok"] == 0
    assert stats["fail"] == 0

if __name__ == "__main__":
    print("Running tests...")
    test_resolve_returns_result()
    print("✓ test_resolve_returns_result passed")
    
    test_resolve_raises_plugin_error() 
    print("✓ test_resolve_raises_plugin_error passed")
    
    test_resolve_returns_no_url_and_no_children()
    print("✓ test_resolve_returns_no_url_and_no_children passed")
    
    test_resolve_raises_captcha_required()
    print("✓ test_resolve_raises_captcha_required passed")
    
    test_link_direct_url_already_set()  
    print("✓ test_link_direct_url_already_set passed")
    
    print("All tests passed!")