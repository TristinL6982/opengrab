"""M12f part 2: /api/extension/config and /api/extension/add endpoints."""
import http.client
import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="og-extapi-"))
os.environ.setdefault("OPENGRAB_DATA_DIR", str(_TMP / "data"))

from opengrab import server  # noqa: E402
from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab.settings import Settings  # noqa: E402

TOKEN = "extensionapitoken0123456789"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture(scope="module")
def srv():
    dl = _TMP / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    settings = Settings(download_dir=str(dl), api_token=TOKEN)
    mgr = DownloadManager(settings)
    mgr.start()
    s = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    s.access.path = _TMP / "pairing.json"
    s.extrules_path = str(_TMP / "extension_rules.json")
    s.port_used = s.start()
    yield s
    s.stop()
    mgr.shutdown()


def _req(port, method, path, body=None):
    hdrs = dict(AUTH)
    data = None
    if body is not None:
        data = json.dumps(body)
        hdrs["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except ValueError:
        return resp.status, raw


def test_config_defaults(srv):
    st, cfg = _req(srv.port_used, "GET", "/api/extension/config")
    assert st == 200, cfg
    assert cfg["version"] == 1
    assert cfg["rules"]["enabled"] is True
    assert "zip" in cfg["rules"]["file_types"]


def test_config_save_roundtrip(srv):
    st, cfg = _req(srv.port_used, "POST", "/api/extension/config",
                   body={"rules": {"bypass_key": "shift", "file_types": [".ISO", "zip"],
                                   "min_size_bytes": 0}})
    assert st == 200, cfg
    assert cfg["rules"]["bypass_key"] == "shift"
    st, cfg = _req(srv.port_used, "GET", "/api/extension/config")
    assert cfg["rules"]["bypass_key"] == "shift"
    assert "iso" in cfg["rules"]["file_types"]
    assert Path(srv.extrules_path).is_file()


def test_add_scopes_cookies(srv):
    body = {
        "url": "http://127.0.0.1:9/files/big.zip",
        "filename": "big.zip",
        "referrer": "http://127.0.0.1:9/page",
        "user_agent": "TestAgent/1.0",
        "auto_download": False,
        "cookies": [
            {"name": "keep", "value": "1", "domain": "127.0.0.1"},
            {"name": "drop", "value": "2", "domain": "other.example"},
        ],
    }
    st, out = _req(srv.port_used, "POST", "/api/extension/add", body=body)
    assert st == 200, out
    assert out["ok"] is True
    assert out["cookies"] == 1
    link = srv.manager.get(out["id"])
    assert link is not None
    info = link.extra["browser"]
    assert [c["name"] for c in info["cookies"]] == ["keep"]
    assert info["user_agent"] == "TestAgent/1.0"
    assert info["referrer"] == "http://127.0.0.1:9/page"


def test_add_rejects_non_http(srv):
    st, out = _req(srv.port_used, "POST", "/api/extension/add",
                   body={"url": "javascript:alert(1)"})
    assert st == 400
    st, out = _req(srv.port_used, "POST", "/api/extension/add", body={})
    assert st == 400
