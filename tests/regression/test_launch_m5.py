"""M5a regression tests: exclusive port binding, no CLI port persistence, launch nonce check."""

import http.client
import json
import os
import tempfile
from pathlib import Path
import pytest
import threading

_TMP = Path(tempfile.mkdtemp(prefix="og-launch-"))
os.environ["OPENGRAB_DATA_DIR"] = str(_TMP / "data")
os.environ["APPDATA"] = str(_TMP / "appdata")
(_TMP / "appdata").mkdir(exist_ok=True)

from opengrab import server
from opengrab.engine.manager import DownloadManager
from opengrab.settings import Settings


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp, data


def test_two_apis_on_same_port_raises_OSError():
    """Test that starting a second ApiServer on the same fixed port raises OSError."""
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    mgr = DownloadManager(settings)
    mgr.start()
    srv1 = server.ApiServer(settings, mgr, host="127.0.0.1", port=5679)
    port1 = srv1.start()
    
    # Trying to start second server should raise OSError
    srv2 = server.ApiServer(settings, DownloadManager(settings), host="127.0.0.1", port=port1)
    with pytest.raises(OSError):
        srv2.start()
    
    srv1.stop()
    mgr.shutdown()


def test_ping_returns_nonce():
    """Test that /api/ping returns the server's nonce."""
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    mgr = DownloadManager(settings)
    mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    port = srv.start()
    
    resp, data = _req(port, "GET", "/api/ping")
    assert resp.status == 200
    parsed = json.loads(data)
    assert "nonce" in parsed
    assert parsed["nonce"] == srv.nonce
    
    srv.stop()
    mgr.shutdown()


def test_verify_own_server_ok():
    """Test that verify_own_server returns True with correct nonce."""
    from opengrab.app import verify_own_server
    
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    mgr = DownloadManager(settings)
    mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    port = srv.start()
    
    assert verify_own_server(port, srv.nonce, settings.base_path) == True
    
    srv.stop()
    mgr.shutdown()


def test_verify_own_server_wrong_nonce():
    """Test that verify_own_server raises RuntimeError with wrong nonce."""
    from opengrab.app import verify_own_server
    
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    mgr = DownloadManager(settings)
    mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    port = srv.start()
    
    with pytest.raises(RuntimeError):
        verify_own_server(port, "wrong-nonce", settings.base_path)
    
    srv.stop()
    mgr.shutdown()


def test_start_server_not_persists_cli_port():
    """Test that start_server() does NOT save CLI-port settings."""
    from opengrab.app import start_server
    
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    
    # Save original settings
    original_port = settings.api_port
    original_host = settings.api_host
    
    # Start with CLI args (should not save to settings)
    _, _, _, bound_port = start_server(settings, host="127.0.0.1", port=5680)
    
    # Check the port was NOT saved to settings
    assert settings.api_port == original_port
    assert settings.api_host == original_host
    
    # Also clean up by stopping the server
    import time
    time.sleep(0.1)  # Brief delay to ensure it's not running anymore


if __name__ == "__main__":
    pytest.main([__file__, "-v"])