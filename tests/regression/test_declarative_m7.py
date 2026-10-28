import json
import os
import tempfile
from unittest.mock import Mock

import pytest

from opengrab.plugins.declarative import DeclarativePlugin, load_templates_dir, TemplateError
from opengrab.plugins.base import PluginContext


class FakeResp:
    def __init__(self, status_code, text, url):
        self.status_code = status_code
        self.text = text
        self.url = url


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.urls = []

    def get(self, url, **kw):
        self.urls.append(url)
        return self.responses.get(url, FakeResp(200, "", url))


def test_supports():
    T = {
        "id": "exhost",
        "name": "ExHost",
        "site": "exhost.com",
        "url_pattern": r"/f/\w+",
        "filename_regex": r'<h1 class="name">([^<]+)</h1>',
        "size_regex": r'Size: ([\d,]+) bytes',
        "direct_url_regex": r'href="([^"]+)" id="dl"',
        "offline_markers": ["File not found"],
        "headers": {"Referer": "https://exhost.com/"}
    }
    
    plugin = DeclarativePlugin(T)

    # Test positive matches
    assert plugin.supports("https://exhost.com/f/abc123")
    assert plugin.supports("https://www.exhost.com/f/abc")

    # Test negative matches
    assert not plugin.supports("https://exhost.com/about")
    assert not plugin.supports("https://other.com/f/abc")


def test_grab():
    T = {
        "id": "exhost",
        "name": "ExHost",
        "site": "exhost.com",
        "url_pattern": r"/f/\w+",
        "filename_regex": r'<h1 class="name">([^<]+)</h1>',
        "size_regex": r'Size: ([\d,]+) bytes',
        "direct_url_regex": r'href="([^"]+)" id="dl"',
        "offline_markers": ["File not found"],
        "headers": {"Referer": "https://exhost.com/"}
    }

    plugin = DeclarativePlugin(T)

    text = '<h1 class="name">My File.zip</h1> Size: 1,048,576 bytes <a href="/d/abc123/My%20File.zip" id="dl">'
    client = FakeClient({ "https://exhost.com/f/abc123": FakeResp(200, text, "https://exhost.com/f/abc123") })
    ctx = PluginContext(client, None)

    result = plugin.grab("https://exhost.com/f/abc123", ctx)
    
    assert result.title == "My File.zip"
    assert result.filename == "My File.zip"
    assert result.size == 1048576
    assert result.site == "exhost.com"
    assert result.plugin_id == "exhost"
    assert result.direct_url == "https://exhost.com/d/abc123/My%20File.zip"
    assert result.direct_headers == {"Referer": "https://exhost.com/"}


def test_offline_marker_raises_error():
    T = {
        "id": "exhost",
        "name": "ExHost",
        "site": "exhost.com",
        "url_pattern": r"/f/\w+",
        "filename_regex": r'<h1 class="name">([^<]+)</h1>',
        "size_regex": r'Size: ([\d,]+) bytes',
        "direct_url_regex": r'href="([^"]+)" id="dl"',
        "offline_markers": ["File not found", "Not found"],
        "headers": {"Referer": "https://exhost.com/"}
    }

    plugin = DeclarativePlugin(T)

    text = 'File not found'
    client = FakeClient({ "https://exhost.com/f/abc123": FakeResp(200, text, "https://exhost.com/f/abc123") })
    ctx = PluginContext(client, None)

    with pytest.raises(Exception) as excinfo:
        plugin.grab("https://exhost.com/f/abc123", ctx)
    assert "offline" in str(excinfo.value)


def test_404_raises_error():
    T = {
        "id": "exhost",
        "name": "ExHost",
        "site": "exhost.com",
        "url_pattern": r"/f/\w+",
        "filename_regex": r'<h1 class="name">([^<]+)</h1>',
        "size_regex": r'Size: ([\d,]+) bytes',
        "direct_url_regex": r'href="([^"]+)" id="dl"',
        "offline_markers": ["File not found"],
        "headers": {"Referer": "https://exhost.com/"}
    }

    plugin = DeclarativePlugin(T)

    client = FakeClient({ "https://exhost.com/f/abc123": FakeResp(404, "", "https://exhost.com/f/abc123") })
    ctx = PluginContext(client, None)

    with pytest.raises(Exception) as excinfo:
        plugin.grab("https://exhost.com/f/abc123", ctx)
    assert "offline" in str(excinfo.value)


def test_resolve():
    T = {
        "id": "exhost",
        "name": "ExHost",
        "site": "exhost.com",
        "url_pattern": r"/f/\w+",
        "filename_regex": r'<h1 class="name">([^<]+)</h1>',
        "size_regex": r'Size: ([\d,]+) bytes',
        "direct_url_regex": r'href="([^"]+)" id="dl"',
        "offline_markers": ["File not found"],
        "headers": {"Referer": "https://exhost.com/"}
    }

    plugin = DeclarativePlugin(T)

    text = '<h1 class="name">My File.zip</h1> Size: 1,048,576 bytes <a href="/d/abc123/My%20File.zip" id="dl">'
    client = FakeClient({ "https://exhost.com/f/abc123": FakeResp(200, text, "https://exhost.com/f/abc123") })
    ctx = PluginContext(client, None)

    result = plugin.resolve("https://exhost.com/f/abc123", ctx)
    
    assert result.url == "https://exhost.com/d/abc123/My%20File.zip"
    assert result.filename == "My File.zip"
    assert result.size == 1048576
    assert result.headers == {"Referer": "https://exhost.com/"}


def test_invalid_templates():
    # Test unknown key
    with pytest.raises(TemplateError) as excinfo:
        DeclarativePlugin({
            "id": "exhost",
            "name": "ExHost",
            "site": "exhost.com",
            "direct_url_regex": r"href=\"([^\"]+)\"",
            "bogus": "key"
        })
    assert "unknown key" in str(excinfo.value)

    # Test missing required key
    with pytest.raises(TemplateError) as excinfo:
        DeclarativePlugin({
            "id": "exhost",
            "name": "ExHost",
            "site": "exhost.com"
            # Missing direct_url_regex
        })
    assert "missing required" in str(excinfo.value)

    # Test invalid regex
    with pytest.raises(TemplateError) as excinfo:
        DeclarativePlugin({
            "id": "exhost",
            "name": "ExHost",
            "site": "exhost.com",
            "direct_url_regex": r"href=\"([^\"]+)",
            "filename_regex": r"("
        })
    assert "invalid" in str(excinfo.value)
    
    # Test bad offline_markers
    with pytest.raises(TemplateError) as excinfo:
        DeclarativePlugin({
            "id": "exhost",
            "name": "ExHost",
            "site": "exhost.com",
            "direct_url_regex": r"href=\"([^\"]+)\"",
            "offline_markers": "x"
        })
    assert "offline_markers must be a list" in str(excinfo.value)

    # Test bad headers
    with pytest.raises(TemplateError) as excinfo:
        DeclarativePlugin({
            "id": "exhost",
            "name": "ExHost",
            "site": "exhost.com",
            "direct_url_regex": r"href=\"([^\"]+)\"",
            "headers": "not a dict"
        })
    assert "headers must be a dict" in str(excinfo.value)


def test_load_templates_dir():
    # Create temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create good template file
        good_path = os.path.join(tmpdir, "good.json")
        T = {
            "id": "exhost",
            "name": "ExHost",
            "site": "exhost.com",
            "url_pattern": r"/f/\w+",
            "filename_regex": r'<h1 class="name">([^<]+)</h1>',
            "size_regex": r'Size: ([\d,]+) bytes',
            "direct_url_regex": r'href="([^"]+)" id="dl"',
            "offline_markers": ["File not found"],
            "headers": {"Referer": "https://exhost.com/"}
        }
        with open(good_path, "w", encoding="utf-8") as f:
            json.dump(T, f)

        # Create bad JSON file
        bad_path = os.path.join(tmpdir, "bad.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("{not json")

        # Create duplicate template file
        dup_path = os.path.join(tmpdir, "dup.json")
        with open(dup_path, "w", encoding="utf-8") as f:
            json.dump(T, f)

        # Load templates
        plugins, errors = load_templates_dir(tmpdir)

        # Should have one plugin and two errors (bad.json and dup.json)
        assert len(plugins) == 1
        assert plugins[0].id == "exhost"
        
        # Ensure bad file is in errors 
        assert "bad.json" in errors
        
        # Ensure at least one error contains "duplicate id" (it can be in either file)
        has_duplicate_error = any("duplicate id" in err for err in errors.values())
        assert has_duplicate_error

        # Test missing directory
        plugins, errors = load_templates_dir("/non/existent/directory")
        assert plugins == []
        assert errors == {}