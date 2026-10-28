"""LinkGrabber API tests for M8j."""

import base64
import http.client
import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="og-lg-"))
os.environ["OPENGRAB_DATA_DIR"] = str(_TMP / "data")
os.environ["APPDATA"] = str(_TMP / "appdata")
(_TMP / "appdata").mkdir(exist_ok=True)

from opengrab import server  # noqa: E402
from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab.settings import Settings  # noqa: E402
from opengrab import grabber
from opengrab.containers import encode_rsdf


TEST_TOKEN = "regressiontoken0123456789"
AUTH = {"Authorization": "Bearer " + TEST_TOKEN}


@pytest.fixture(scope="module")
def port():
    dl = _TMP / "downloads"
    dl.mkdir(exist_ok=True)
    settings = Settings(download_dir=str(dl), api_token=TEST_TOKEN)
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


def test_linkgrab_analyze(port):
    # Test POST /api/linkgrab/analyze
    text = "https://a.com/one.zip\nhttps://a.com/one.zip\nhttps://b.com/two.zip"
    body = {"text": text}
    
    resp, data = _req(
        port, 
        "POST", 
        "/api/linkgrab/analyze", 
        body=json.dumps(body),
        headers={"Content-Type": "application/json", **AUTH}
    )
    
    assert resp.status == 200
    result = json.loads(data)
    expected = grabber.analyze_text(text)
    assert result == expected
    assert result["count"] == 2

    # Test without token -> should fail with 401
    resp, data = _req(
        port,
        "POST",
        "/api/linkgrab/analyze",
        body=json.dumps(body),
        headers={"Content-Type": "application/json"}
    )
    
    assert resp.status == 401


def test_linkgrab_container(port):
    # Test POST /api/linkgrab/container with valid RSDF
    links = ["https://c.com/1.zip", "https://c.com/2.zip"]
    data = encode_rsdf(links)
    body = {"filename": "x.rsdf", "data": base64.b64encode(data).decode("ascii")}
    
    resp, data = _req(
        port,
        "POST",
        "/api/linkgrab/container",
        body=json.dumps(body),
        headers={"Content-Type": "application/json", **AUTH}
    )
    
    assert resp.status == 200
    result = json.loads(data)
    assert result["count"] == 2
    all_links = []
    for pkg in result["packages"]:
        all_links.extend(pkg["links"])
    assert set(all_links) == set(links)

    # Test invalid data -> 400
    resp, _ = _req(
        port,
        "POST",
        "/api/linkgrab/container",
        body=json.dumps({"filename": "x.dlc", "data": ""}),
        headers={"Content-Type": "application/json", **AUTH}
    )
    
    assert resp.status == 400
    
    # Test invalid base64 -> 400
    resp, _ = _req(
        port,
        "POST",
        "/api/linkgrab/container",
        body=json.dumps({"filename": "x.rsdf", "data": "%%%not base64"}),
        headers={"Content-Type": "application/json", **AUTH}
    )
    
    assert resp.status == 400


def test_linkgrab_with_existing_links(port):
    # Add a link via /api/urls first
    body = {"urls": ["https://d.com/already.zip"], "auto_download": False}
    _req(
        port,
        "POST",
        "/api/urls",
        body=json.dumps(body),
        headers={"Content-Type": "application/json", **AUTH}
    )
    
    # Now try to analyze text that includes the existing link
    text = "https://d.com/already.zip https://d.com/new.zip"
    body = {"text": text}
    
    resp, data = _req(
        port,
        "POST",
        "/api/linkgrab/analyze",
        body=json.dumps(body),
        headers={"Content-Type": "application/json", **AUTH}
    )
    
    assert resp.status == 200
    result = json.loads(data)
    # Should only return count of 1 since the first link already exists
    assert result["count"] == 1
    
    # The set of links across all packages should contain only the new link  
    all_links = []
    for pkg in result["packages"]:
        all_links.extend(pkg["links"])
    
    assert len(all_links) == 1 
    assert all_links[0] == "https://d.com/new.zip"