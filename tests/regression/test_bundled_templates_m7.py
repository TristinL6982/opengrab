import tempfile
from pathlib import Path

from opengrab.plugins.registry import PluginRegistry


def test_bundled_templates_no_user_dir():
    """Test that bundled templates work when there's no user directory."""
    reg = PluginRegistry()
    # Should not fail even without user dir
    count = reg.scan(force=True)
    assert count >= 0
    
    # The example-host plugin should be available and have origin "bundled"
    plugin = reg.get("example-host")
    assert plugin is not None
    assert plugin.origin == "bundled"


def test_bundled_templates_for_url():
    """Test that bundled templates work for URL matching."""
    reg = PluginRegistry()
    
    # Scan to load bundled templates
    reg.scan(force=True)
    
    # Should find example-host plugin for the specific URL
    plugins = reg.for_url("https://example-host.invalid/f/abc")
    assert len(plugins) >= 1
    plugin_ids = [p.id for p in plugins]
    assert "example-host" in plugin_ids


def test_user_override_bundled_template():
    """Test that user templates can override bundled templates."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
        # Set user directory
        reg = PluginRegistry()
        reg.set_user_dir(tmp_path)
        
        # Create a user template that overrides the bundled one
        override_content = {
            "id": "example-host",
            "name": "User Override",
            "site": "example-host.invalid",
            "description": "User override of bundled template",
            "url_pattern": "^https?://(www\\.)?example-host\\.invalid/f/",
            "direct_url_regex": "href=\"(https?://dl\\.example-host\\.invalid/[^\"]+)\"",
            "filename_regex": "<h1>([^<]+)</h1>",
            "offline_markers": ["File not found"]
        }
        
        import json
        override_file = tmp_path / "example-host.json"
        with open(override_file, "w", encoding="utf-8") as f:
            json.dump(override_content, f)
        
        # Scan to load both bundled and user templates
        reg.scan(force=True)
        
        # Should find the plugin, but now it should be from 'template' origin
        plugin = reg.get("example-host")
        assert plugin is not None
        assert plugin.origin == "template"
        assert plugin.name == "User Override"
        
        # Check that there's no bundled template error
        assert "template:example-host" not in reg.errors


def test_scan_twice():
    """Test scanning twice keeps exactly one plugin and removes bundled errors."""
    reg = PluginRegistry()
    
    # Scan twice
    reg.scan(force=True)
    reg.scan(force=True)
    
    # Count plugins with example-host id 
    count = 0
    for plugin in reg.plugins.values():
        if plugin.id == "example-host":
            count += 1
    
    assert count == 1
    
    # Check that there are no bundled errors
    bundled_errors = [k for k in reg.errors.keys() if k.startswith("bundled:")]
    assert len(bundled_errors) == 0


if __name__ == "__main__":
    test_bundled_templates_no_user_dir()
    test_bundled_templates_for_url()
    test_user_override_bundled_template()
    test_scan_twice()
    print("All tests passed!")