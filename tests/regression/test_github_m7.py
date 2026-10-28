"""
Tests for GitHub plugin.
"""

import pytest

from opengrab.plugins.base import PluginError
from opengrab.plugins.builtin.github import (
    direct_url,
    file_name,
    GitHubPlugin,
)
from opengrab.plugins.registry import PluginRegistry


def test_direct_url():
    assert direct_url("https://github.com/o/r/blob/main/dir/a.txt") == "https://raw.githubusercontent.com/o/r/main/dir/a.txt"
    assert direct_url("https://github.com/o/r/raw/v1.0/a.bin") == "https://raw.githubusercontent.com/o/r/v1.0/a.bin"
    assert direct_url("https://github.com/o/r/releases/download/v2/tool.zip") == "https://github.com/o/r/releases/download/v2/tool.zip"
    assert direct_url("https://raw.githubusercontent.com/o/r/main/a.txt") == "https://raw.githubusercontent.com/o/r/main/a.txt"
    assert direct_url("https://github.com/o/r") is None
    assert direct_url("https://github.com/o/r/releases") is None
    assert direct_url("https://example.com/o/r/blob/main/a.txt") is None


def test_file_name():
    assert file_name("https://raw.githubusercontent.com/o/r/main/dir/a.txt") == "a.txt"


def test_github_plugin_supports():
    assert GitHubPlugin().supports("https://github.com/o/r/blob/main/a.txt") is True
    assert GitHubPlugin().supports("https://example.com/x") is False


def test_github_plugin_grab():
    result = GitHubPlugin().grab("https://github.com/o/r/blob/main/dir/a.txt", None)
    assert result.direct_url == "https://raw.githubusercontent.com/o/r/main/dir/a.txt"
    assert result.plugin_id == "github"
    assert result.title == "a.txt"


def test_github_plugin_resolve():
    result = GitHubPlugin().resolve("https://github.com/o/r/blob/main/dir/a.txt", None)
    assert result.url == "https://raw.githubusercontent.com/o/r/main/dir/a.txt"


def test_github_plugin_grab_error():
    with pytest.raises(PluginError):
        GitHubPlugin().grab("https://example.com/x", None)


def test_registry_discovery():
    registry = PluginRegistry()
    registry.scan(force=True)
    assert "github" in registry.plugins