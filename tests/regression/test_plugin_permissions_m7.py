"""
Tests for plugin permissions model.
"""

import pytest
from opengrab.plugins.permissions import (
    PermissionError_,
    Permissions,
    parse_permissions,
    requested_beyond,
    load_grants,
    save_grants
)


def test_allows_url():
    # Test pattern matching
    p = Permissions(hosts=["*.example.com"])
    assert p.allows_url("https://a.example.com/x") is True
    assert p.allows_url("https://example.com/x") is False
    assert p.allows_url("https://evil.com/") is False
    
    # Test case insensitivity 
    p = Permissions(hosts=["Example.COM"])
    assert p.allows_url("https://example.com/") is True


def test_check_url():
    p = Permissions(hosts=["*.example.com"])
    p.check_url("https://a.example.com/x")  # Should not raise
    with pytest.raises(PermissionError_, match="host not allowed: evil.com"):
        p.check_url("https://evil.com/")


def test_check_files():
    p = Permissions()
    with pytest.raises(PermissionError_, match="file access not allowed"):
        p.check_files()
    p = Permissions(files=True)
    p.check_files()  # Should not raise


def test_check_cookies():
    p = Permissions()
    with pytest.raises(PermissionError_, match="cookie access not allowed"):
        p.check_cookies()
    p = Permissions(cookies=True)
    p.check_cookies()  # Should not raise


def test_parse_permissions():
    result = parse_permissions({"hosts": [" a.com "], "files": True})
    assert result.to_dict() == {"hosts": ["a.com"], "files": True, "cookies": False}


def test_parse_permissions_invalid_hosts():
    with pytest.raises(ValueError, match="hosts must be"):
        parse_permissions({"hosts": "a.com"})


def test_parse_permissions_invalid_files():
    with pytest.raises(ValueError, match="files must be a boolean"):
        parse_permissions({"files": "yes"})


def test_parse_permissions_invalid_cookies():
    with pytest.raises(ValueError, match="cookies must be a boolean"):
        parse_permissions({"cookies": "yes"})


def test_requested_beyond():
    requested = Permissions(hosts=["a.com", "b.com"], files=True, cookies=True)
    granted = Permissions(hosts=["a.com"], cookies=True)
    result = requested_beyond(requested, granted)
    assert result == ["files", "host:b.com"]


def test_load_save_grants(tmp_path):
    # Test saving and loading
    grants = {"p1": Permissions(hosts=["a.com"])}
    save_grants(tmp_path / "sub" / "grants.json", grants)
    loaded = load_grants(tmp_path / "sub" / "grants.json")
    assert loaded == grants
    
    # Test loading non-existent file
    assert load_grants(tmp_path / "missing.json") == {}