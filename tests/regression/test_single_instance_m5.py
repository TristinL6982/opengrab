"""M5b regression tests: single instance lock with URL forwarding and busy-port fallback."""

import http.client
import json
import os
import tempfile
from pathlib import Path
import pytest
import threading
import socket

_TMP = Path(tempfile.mkdtemp(prefix="og-single-instance-"))
os.environ["OPENGRAB_DATA_DIR"] = str(_TMP / "data")
os.environ["APPDATA"] = str(_TMP / "appdata")
(_TMP / "appdata").mkdir(exist_ok=True)

from opengrab import server
from opengrab.engine.manager import DownloadManager
from opengrab.settings import Settings
from opengrab.single_instance import acquire, release, forward_to_running


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp, data


def test_acquire_release():
    """Test acquire() and release() functions."""
    handle1 = acquire("test-lock")
    assert handle1 is not None
    handle2 = acquire("test-lock")
    assert handle2 is None
    release(handle1)
    handle3 = acquire("test-lock")
    assert handle3 is not None
    release(handle3)


def test_forward_to_running():
    """Test forward_to_running() function with a real server."""
    # Start a real server
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    mgr = DownloadManager(settings)
    mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    port = srv.start()
    
    try:
        # Test forwarding to this running server
        urls = ["https://example.com/file1", "https://example.com/file2"]
        success = forward_to_running(urls, "127.0.0.1", port, settings.api_token)
        assert success is True
        
        # Verify URLs were added by accessing the links endpoint with the token
        headers = {"Authorization": f"Bearer {settings.api_token}"}
        resp, data = _req(port, "GET", "/api/links", headers=headers)
        assert resp.status == 200
        links = json.loads(data)
        assert len(links) >= 2  # At least the two we added
    finally:
        srv.stop()
        mgr.shutdown()


def test_forward_to_running_wrong_token():
    """Test forward_to_running() fails with wrong token."""
    # Start a real server
    dl = _TMP / "downloads" 
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    mgr = DownloadManager(settings)
    mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    port = srv.start()
    
    try:
        # Test forwarding with wrong token
        urls = ["https://example.com/file1"]
        success = forward_to_running(urls, "127.0.0.1", port, "wrong-token")
        assert success is False
    finally:
        srv.stop()
        mgr.shutdown()


def test_start_server_fallback():
    """Test that start_server() falls back to a different port when the configured one is busy."""
    # Bind a socket to a port first to make it busy 
    busy_port = 5681
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
    sock.bind(("127.0.0.1", busy_port))
    sock.listen(1)
    
    try:
        # Try to start the OpenGrab server on that port - it should fall back
        dl = _TMP / "downloads"
        dl.mkdir(exist_ok=True)
        settings = Settings(download_dir=str(dl))
        mgr = DownloadManager(settings)
        mgr.start()
        srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=busy_port)
        
        # This call should work even though we've bound the port ourselves
        with pytest.warns():  # Warning expected 
            port = srv.start()
            
        # The server should have picked a different port  
        assert port != busy_port
        assert port > 0
        
    finally:
        sock.close()
        mgr.shutdown()


def test_start_server_raises_on_cli_port():
    """Test that start_server() still raises OSError on explicitly set CLI ports."""
    # Bind a socket to a port first to make it busy 
    busy_port = 5682
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
    sock.bind(("127.0.0.1", busy_port))
    sock.listen(1)
    
    try:
        # Try to start the OpenGrab server on that port with explicit CLI args - should raise
        dl = _TMP / "downloads" 
        dl.mkdir(exist_ok=True)
        settings = Settings(download_dir=str(dl))
        mgr = DownloadManager(settings)
        mgr.start()
        srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=busy_port)
        
        with pytest.raises(OSError):
            srv.start()
            
    finally:
        sock.close()
        mgr.shutdown()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])