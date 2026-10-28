import pytest
from opengrab.plugins.builtin.pixeldrain import file_id, PixeldrainPlugin
from opengrab.plugins.base import PluginError
from opengrab.plugins.registry import PluginRegistry


def test_file_id():
    assert file_id("https://pixeldrain.com/u/abc123") == "abc123"
    assert file_id("https://www.pixeldrain.com/u/XyZ9") == "XyZ9"
    assert file_id("https://pixeldrain.com/api/file/abc123?download") == "abc123"
    assert file_id("https://pixeldrain.com/l/abc123") is None
    assert file_id("https://example.com/u/abc123") is None


def test_supports():
    plugin = PixeldrainPlugin()
    assert plugin.supports("https://pixeldrain.com/u/abc123") is True
    assert plugin.supports("https://example.com/u/abc123") is False


def test_grab():
    plugin = PixeldrainPlugin()
    result = plugin.grab("https://pixeldrain.com/u/abc123", None)
    assert result.direct_url == "https://pixeldrain.com/api/file/abc123?download"
    assert result.plugin_id == "pixeldrain"
    assert result.title == "abc123"
    assert result.direct_headers == {"referer": "https://pixeldrain.com/u/abc123"}


def test_resolve():
    plugin = PixeldrainPlugin()
    result = plugin.resolve("https://pixeldrain.com/u/abc123", None)
    assert result.url == "https://pixeldrain.com/api/file/abc123?download"
    assert result.headers == {"referer": "https://pixeldrain.com/u/abc123"}


def test_grab_error():
    plugin = PixeldrainPlugin()
    with pytest.raises(PluginError):
        plugin.grab("https://example.com/x", None)


def test_registry_discovery():
    registry = PluginRegistry()
    registry.scan(force=True)
    assert "pixeldrain" in registry.plugins