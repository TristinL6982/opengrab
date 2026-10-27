import pytest
import tempfile
import http.client
from pathlib import Path

# Add src to path so we can import opengrab modules
import sys
sys.path.insert(0, 'src')

import opengrab
from opengrab.settings import Settings
from opengrab.download import DownloadManager
from opengrab.api import server


@pytest.fixture(scope="module")
def api_server():
    with tempfile.TemporaryDirectory() as tmp_dir:
        data_dir = Path(tmp_dir) / "data"
        appdata_dir = Path(tmp_dir) / "appdata"
        
        # Set directories for the session
        env = {
            'OPENGRAB_DATA_DIR': str(data_dir),
            'APPDATA': str(appdata_dir)
        }
        
        # Import after setting environment variables
        import opengrab.settings
        import opengrab.download
        import opengrab.api.server

        # Create settings with temp download directory
        settings = Settings(download_dir=Path(tmp_dir) / "downloads")
        mgr = DownloadManager(settings)
        port = server.ApiServer(settings, mgr, host="127.0.0.1", port=0).start()

        yield port, mgr, settings

        mgr.stop()


@pytest.mark.xfail(strict=True, reason="C4: settings does not leak token")
def test_c4_settings_does_not_leak_token(api_server):
    port, mgr, settings = api_server
    conn = http.client.HTTPConnection("localhost", port)
    conn.request("GET", "/api/settings", headers={"Origin": "https://evil.example"})
    response = conn.getresponse()
    data = response.read().decode()
    # Simple check for token leakage
    assert "api_token" not in data or '"api_token": ""' in data
    assert response.getheader("Access-Control-Allow-Origin") != "*"
    conn.close()


@pytest.mark.xfail(strict=True, reason="C4: settings write requires token")
def test_c4_settings_write_requires_token(api_server):
    port, mgr, settings = api_server
    conn = http.client.HTTPConnection("localhost", port)
    conn.request("POST", "/api/settings", body='{"terminal_enabled": true}', headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    assert response.status in [401, 403]
    conn.close()


@pytest.mark.xfail(strict=True, reason="C4: rejects foreign host")
def test_c4_rejects_foreign_host(api_server):
    port, mgr, settings = api_server
    conn = http.client.HTTPConnection("localhost", port)
    conn.request("GET", "/api/ping", headers={"Host": "attacker.example"})
    response = conn.getresponse()
    assert str(response.status).startswith("4")
    conn.close()


@pytest.mark.xfail(strict=True, reason="C5: invalid setting rejected")
def test_c5_invalid_setting_rejected():
    from opengrab.settings import Settings
    s = Settings()
    with pytest.raises(Exception):
        s.max_parallel = "lots"