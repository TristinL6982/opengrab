"""C4/C5 regression tests: local API security and settings validation.

Each test is xfail(strict=True) until M4 fixes the bug; when a fix lands the
test starts passing and strict xfail forces the marker to be removed.
"""
import http.client
import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="og-sec-"))
os.environ["OPENGRAB_DATA_DIR"] = str(_TMP / "data")
os.environ["APPDATA"] = str(_TMP / "appdata")
(_TMP / "appdata").mkdir(exist_ok=True)

from opengrab import server  # noqa: E402
from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def port():
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl))
    mgr = DownloadManager(settings)
    mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    p = srv.start()
    yield p
    srv.stop()
    mgr.shutdown()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp, data


def test_c4_settings_does_not_leak_token(port):
    resp, data = _req(port, "GET", "/api/settings", headers={"Origin": "https://evil.example"})
    token = json.loads(data).get("api_token") if resp.status == 200 else None
    assert not token, f"token leaked: status={resp.status}"
    assert resp.getheader("Access-Control-Allow-Origin") != "*"


@pytest.mark.xfail(strict=True, reason="C4: POST /api/settings accepted from loopback without a token")
def test_c4_settings_write_requires_token(port):
    resp, _ = _req(port, "POST", "/api/settings", body=json.dumps({"terminal_enabled": True}),
                   headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
    assert resp.status in (401, 403), f"status={resp.status}"


def test_c4_rejects_foreign_host(port):
    resp, _ = _req(port, "GET", "/api/ping", headers={"Host": "attacker.example"})
    assert 400 <= resp.status < 500, f"status={resp.status}"


@pytest.mark.xfail(strict=True, reason="C5: Settings does not validate assignment, bad values persist")
def test_c5_invalid_setting_rejected():
    s = Settings()
    with pytest.raises(Exception):
        s.max_parallel = "lots"
