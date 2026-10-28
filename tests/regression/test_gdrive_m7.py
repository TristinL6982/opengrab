import pytest
from opengrab.plugins.base import PluginError
from opengrab.plugins.registry import PluginRegistry
from opengrab.plugins.builtin.gdrive import file_id, direct_url, GoogleDrivePlugin

FID = "1AbCdEfGhIjKlMnOp_-9"


def test_file_id():
    assert file_id(f"https://drive.google.com/file/d/{FID}/view?usp=sharing") == FID
    assert file_id(f"https://drive.google.com/open?id={FID}") == FID
    assert file_id(f"https://drive.google.com/uc?id={FID}&export=download") == FID
    assert file_id("https://drive.google.com/drive/folders/abc") is None
    assert file_id(f"https://example.com/file/d/{FID}/view") is None
    assert file_id("https://drive.google.com/open?id=short") is None


def test_direct_url():
    assert direct_url(FID) == f"https://drive.usercontent.google.com/download?id={FID}&export=download&confirm=t"


def test_plugin_supports():
    assert GoogleDrivePlugin().supports(f"https://drive.google.com/file/d/{FID}/view") is True
    assert GoogleDrivePlugin().supports("https://example.com/x") is False


def test_plugin_grab():
    result = GoogleDrivePlugin().grab(f"https://drive.google.com/file/d/{FID}/view", None)
    assert result.direct_url == direct_url(FID)
    assert result.plugin_id == "gdrive"
    assert result.title == FID


def test_plugin_resolve():
    result = GoogleDrivePlugin().resolve(f"https://drive.google.com/file/d/{FID}/view", None)
    assert result.url == direct_url(FID)


def test_plugin_grab_error():
    with pytest.raises(PluginError):
        GoogleDrivePlugin().grab("https://example.com/x", None)


def test_registry_discovery():
    registry = PluginRegistry()
    registry.scan(force=True)
    assert "gdrive" in registry.plugins