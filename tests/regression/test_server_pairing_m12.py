"""M12c part 2: server endpoints for device pairing and device tokens."""
import http.client
import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="og-pair-"))
os.environ.setdefault("OPENGRAB_DATA_DIR", str(_TMP / "data"))

from opengrab import server  # noqa: E402
from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab.settings import Settings  # noqa: E402

MASTER = "pairingmastertoken0123456789"
MASTER_AUTH = {"Authorization": "Bearer " + MASTER}


@pytest.fixture(scope="module")
def srv():
    dl = _TMP / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    settings = Settings(download_dir=str(dl), api_token=MASTER)
    mgr = DownloadManager(settings)
    mgr.start()
    s = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    s.access.path = _TMP / "pairing.json"
    s.port_used = s.start()
    yield s
    s.stop()
    mgr.shutdown()


def _req(port, method, path, body=None, headers=None):
    hdrs = dict(headers or {})
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


def _pair(srv, name="Phone"):
    st, start = _req(srv.port_used, "POST", "/api/pair/start", body={}, headers=MASTER_AUTH)
    assert st == 200, start
    st, claim = _req(srv.port_used, "POST", "/api/pair/claim",
                     body={"code": start["code"], "name": name})
    assert st == 200, claim
    return claim


def test_server_has_access_gate(srv):
    assert srv.access.master_token == MASTER
    assert srv.access.policy.allow_remote_http is False


def test_pair_start_needs_master_token(srv):
    st, _ = _req(srv.port_used, "POST", "/api/pair/start", body={})
    assert st == 401
    st, body = _req(srv.port_used, "POST", "/api/pair/start", body={}, headers=MASTER_AUTH)
    assert st == 200 and "-" in body["code"] and body["expires_in"] > 0


def test_claim_gives_working_device_token_and_persists(srv):
    claim = _pair(srv, "Phone A")
    assert claim["device"]["name"] == "Phone A" and claim["token"]
    st, _ = _req(srv.port_used, "GET", "/api/settings",
                 headers={"Authorization": "Bearer " + claim["token"]})
    assert st == 200
    saved = json.loads((_TMP / "pairing.json").read_text(encoding="utf-8"))
    assert "Phone A" in json.dumps(saved)


def test_bad_code_is_rejected(srv):
    st, body = _req(srv.port_used, "POST", "/api/pair/claim",
                    body={"code": "ZZZZ-ZZZZ", "name": "x"})
    assert st == 400 and "error" in body


def test_device_token_cannot_manage_pairing(srv):
    dev = {"Authorization": "Bearer " + _pair(srv, "Tablet")["token"]}
    st, _ = _req(srv.port_used, "POST", "/api/pair/start", body={}, headers=dev)
    assert st == 403
    st, _ = _req(srv.port_used, "GET", "/api/devices", headers=dev)
    assert st == 403


def test_list_and_revoke_devices(srv):
    claim = _pair(srv, "Laptop")
    dev_id = claim["device"]["id"]
    st, body = _req(srv.port_used, "GET", "/api/devices", headers=MASTER_AUTH)
    assert st == 200 and dev_id in [d["id"] for d in body["devices"]]
    st, body = _req(srv.port_used, "DELETE", "/api/devices/" + dev_id, headers=MASTER_AUTH)
    assert st == 200 and body["revoked"] == dev_id
    st, _ = _req(srv.port_used, "GET", "/api/settings",
                 headers={"Authorization": "Bearer " + claim["token"]})
    assert st == 401
    st, _ = _req(srv.port_used, "DELETE", "/api/devices/" + dev_id, headers=MASTER_AUTH)
    assert st == 404


def test_authorize_accepts_master_and_rejects_garbage(srv):
    assert srv.authorize("127.0.0.1", {"Authorization": "Bearer " + MASTER})
    assert not srv.authorize("127.0.0.1", {"Authorization": "Bearer nope"})
    assert not srv.authorize("127.0.0.1", {})
