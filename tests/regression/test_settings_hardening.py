import json
import pytest
import http.client
from pathlib import Path
from src.opengrab.settings import Settings
from src.opengrab.server import ApiServer, DownloadManager
from tests.regression.test_api_security import port

def test_settings_write_whitelist(port):
    """Test that settings writes are validated and filtered"""
    
    # Test 1: terminal_enabled should be ignored 
    resp, data = _req(port, "POST", "/api/settings", body=json.dumps({"terminal_enabled": True}), headers={"Content-Type": "application/json"})
    assert resp.status == 200
    result = json.loads(data)
    assert "ignored" in result
    assert "terminal_enabled" in result["ignored"]
    
    # Verify that the setting was not actually changed  
    resp, data = _req(port, "GET", "/api/settings")
    settings = json.loads(data)
    assert not settings.get("terminal_enabled", False)

def test_settings_write_validation(port):
    """Test validation of settings values"""
    
    # Test with invalid value for max_parallel
    resp, data = _req(port, "POST", "/api/settings", body=json.dumps({"max_parallel": "abc"}), headers={"Content-Type": "application/json"})
    assert resp.status == 400
    
    # Verify that the setting was not changed  
    resp, data = _req(port, "GET", "/api/settings")
    settings = json.loads(data)
    assert settings.get("max_parallel") == 3  # default value

def test_settings_write_ignore_unknown_keys(port):
    """Test unknown keys are ignored"""
    
    # POST with unknown key
    resp, data = _req(port, "POST", "/api/settings", body=json.dumps({"no_such_key": 1}), headers={"Content-Type": "application/json"})
    assert resp.status == 200
    result = json.loads(data)
    assert "ignored" in result
    assert "no_such_key" in result["ignored"]

def test_load_settings_with_corrupt_file(monkeypatch, tmp_path):
    """Test load_settings backs up corrupt settings.json"""
    
    # Create corrupted settings file
    corrupted = tmp_path / "settings.json"
    corrupted.write_text("{not json")
    
    # Patch app_data_dir to return temp path
    monkeypatch.setattr("src.opengrab.settings.app_data_dir", lambda: tmp_path)
    
    # Call load_settings (should backup corrupt file and return defaults)
    from src.opengrab.settings import load_settings
    
    settings = load_settings()
    
    # Check that a backup file was created
    backup_files = list(tmp_path.glob("settings.json.bad-*"))
    assert len(backup_files) == 1
    assert backup_files[0].name.startswith("settings.json.bad-")
    
    # Check that we got default settings back  
    assert isinstance(settings, Settings)
    assert settings.max_parallel == 3
    
def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp, data