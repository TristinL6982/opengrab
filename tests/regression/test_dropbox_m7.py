"""
Tests for Dropbox plugin.
"""

import pytest
from opengrab.plugins.builtin.dropbox import is_share_link, direct_url, file_name, DropboxPlugin
from opengrab.plugins.base import PluginError
from opengrab.plugins.registry import PluginRegistry


def test_is_share_link():
    assert is_share_link("https://www.dropbox.com/s/abc123/report.pdf?dl=0") is True
    assert is_share_link("https://www.dropbox.com/scl/fi/xyz789/My%20File.zip?rlkey=k1&dl=0") is True
    assert is_share_link("https://www.dropbox.com/home") is False
    assert is_share_link("https://example.com/s/abc123/report.pdf") is False


def test_direct_url():
    assert direct_url("https://www.dropbox.com/s/abc123/report.pdf?dl=0") == "https://www.dropbox.com/s/abc123/report.pdf?dl=1"
    assert direct_url("https://dropbox.com/scl/fi/xyz789/My%20File.zip?rlkey=k1&dl=0") == "https://www.dropbox.com/scl/fi/xyz789/My%20File.zip?rlkey=k1&dl=1"
    assert direct_url("https://www.dropbox.com/s/abc123/report.pdf") == "https://www.dropbox.com/s/abc123/report.pdf?dl=1"


def test_file_name():
    assert file_name("https://www.dropbox.com/scl/fi/xyz789/My%20File.zip?rlkey=k1&dl=0") == "My File.zip"


def test_dropbox_grab():
    plugin = DropboxPlugin()
    result = plugin.grab("https://www.dropbox.com/s/abc123/report.pdf?dl=0", None)
    assert result.direct_url == "https://www.dropbox.com/s/abc123/report.pdf?dl=1"
    assert result.plugin_id == "dropbox"
    assert result.title == "report.pdf"
    resolve_result = plugin.resolve("https://www.dropbox.com/s/abc123/report.pdf?dl=0", None)
    assert resolve_result.url == "https://www.dropbox.com/s/abc123/report.pdf?dl=1"


def test_dropbox_grab_error():
    with pytest.raises(PluginError):
        DropboxPlugin().grab("https://example.com/x", None)


def test_registry_discovery():
    registry = PluginRegistry()
    registry.scan(force=True)
    assert "dropbox" in registry.plugins