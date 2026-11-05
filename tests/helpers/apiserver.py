"""Shared helpers for tests that run a real ApiServer on an ephemeral port."""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import tempfile
from pathlib import Path

MASTER = "wiringmastertoken0123456789"
MASTER_AUTH = {"Authorization": "Bearer " + MASTER}


@contextlib.contextmanager
def running_server(settings_overrides=None, data_dir: Path | None = None, start_manager=True):
    """Start DownloadManager + ApiServer with an isolated data dir."""
    from opengrab import server
    from opengrab.engine.manager import DownloadManager
    from opengrab.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="og-wire-"))
    data_dir = data_dir or tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    old = os.environ.get("OPENGRAB_DATA_DIR")
    os.environ["OPENGRAB_DATA_DIR"] = str(data_dir)
    dl = tmp / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    # the real-browser captcha solver is on by default (owner decision 2026-11-05);
    # tests must never launch a browser, so it is off unless a test asks for it
    overrides = {"captcha_browser_enabled": False, **(settings_overrides or {})}
    settings = Settings(download_dir=str(dl), api_token=MASTER, **overrides)
    mgr = DownloadManager(settings)
    if start_manager:
        mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    srv.access.path = tmp / "pairing.json"
    srv.port_used = srv.start()
    srv.tmp = tmp
    try:
        yield srv
    finally:
        srv.stop()
        mgr.shutdown()
        if old is None:
            os.environ.pop("OPENGRAB_DATA_DIR", None)
        else:
            os.environ["OPENGRAB_DATA_DIR"] = old


def req(srv, method, path, body=None, headers=None, auth=True):
    hdrs = dict(MASTER_AUTH if auth is True else (auth or {}))
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body)
        hdrs["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", srv.port_used, timeout=15)
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except ValueError:
        return resp.status, raw


def pair_device(srv, name="Phone") -> dict:
    """Pair a device and return {"Authorization": "Bearer <device token>"}."""
    st, start = req(srv, "POST", "/api/pair/start", body={})
    assert st == 200, start
    st, claim = req(srv, "POST", "/api/pair/claim", body={"code": start["code"], "name": name}, auth=False)
    assert st == 200, claim
    return {"Authorization": "Bearer " + claim["token"]}
