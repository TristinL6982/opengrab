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


def _busy_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def test_start_server_fallback(caplog, monkeypatch):
    """start_server() falls back to a free port when the configured port is busy, without saving it."""
    import logging
    from opengrab.app import start_server
    import opengrab.settings as og_settings
    tmp_path = Path(tempfile.mkdtemp(prefix="og-m5b-settings-"))
    monkeypatch.setattr(og_settings, "app_data_dir", lambda: tmp_path)
    sock, busy_port = _busy_socket()
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    settings.api_host = "127.0.0.1"
    settings.api_port = busy_port
    settings.save()
    settings_file = tmp_path / "settings.json"
    saved_before = json.loads(settings_file.read_text(encoding="utf-8")).get("api_port")
    srv = mgr = None
    try:
        with caplog.at_level(logging.WARNING, logger="opengrab.app"):
            _, mgr, srv, port = start_server(settings)
        assert port != busy_port and port > 0
        assert settings.api_port == busy_port
        saved_after = json.loads(settings_file.read_text(encoding="utf-8")).get("api_port")
        assert saved_after == saved_before == busy_port
        assert any(str(busy_port) in r.getMessage() and str(port) in r.getMessage()
                   for r in caplog.records)
        resp, _ = _req(port, "GET", "/api/ping")
        assert resp.status == 200
    finally:
        if srv:
            srv.stop()
        if mgr:
            mgr.shutdown()
        sock.close()


def test_start_server_raises_on_cli_port():
    """start_server(port=<busy>) raises OSError: no silent fallback for an explicit CLI port."""
    from opengrab.app import start_server
    sock, busy_port = _busy_socket()
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    try:
        with pytest.raises(OSError):
            start_server(settings, host="127.0.0.1", port=busy_port)
    finally:
        sock.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])