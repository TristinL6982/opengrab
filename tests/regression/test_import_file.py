"""Files dropped onto the window are uploaded to POST /api/import-file."""
import http.client
import os
import re
import time
from pathlib import Path

import pytest

from tests.helpers.apiserver import MASTER_AUTH, req, running_server
from tests.regression.test_torrent_local_file import SETTINGS, _make_torrent

lt = pytest.importorskip("libtorrent")
STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"


def post(srv, name, data, auth=True, length=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port_used, timeout=15)
    hdrs = dict(MASTER_AUTH) if auth else {}
    hdrs["Content-Type"] = "application/octet-stream"
    if length is not None:
        hdrs["Content-Length"] = str(length)
    from urllib.parse import quote
    conn.request("POST", "/api/import-file?name=" + quote(name), body=data, headers=hdrs)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    import json
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def test_valid_torrent_is_stored_by_sha1_and_started(tmp_path):
    tf = _make_torrent(tmp_path)
    data = tf.read_bytes()
    with running_server(SETTINGS) as srv:
        st, out = post(srv, "Road Craft [x].torrent", data)
        assert st == 200 and out["kind"] == "torrent", out
        link = srv.manager.get(out["id"])
        copy = Path(link.extra["torrent"]["torrent_file"])
        import hashlib
        assert copy.name == hashlib.sha1(data).hexdigest() + ".torrent"
        assert copy.parent == srv.manager.data_dir / "torrents" and copy.read_bytes() == data
        assert link.plugin == "torrent" and link.title == "Road Craft [x]"
        end = time.time() + 30
        while time.time() < end and not (srv.manager.get(link.id).extra["torrent"].get("info_hash")):
            time.sleep(0.2)
        assert srv.manager.get(link.id).status.value != "error"


def test_filename_is_never_used_as_a_path(tmp_path):
    data = _make_torrent(tmp_path).read_bytes()
    with running_server(SETTINGS) as srv:
        st, out = post(srv, r"..\..\evil/../x.torrent", data)
        assert st == 200, out
        assert Path(srv.manager.get(out["id"]).extra["torrent"]["torrent_file"]).parent.name == "torrents"
        assert not list(srv.tmp.rglob("x.torrent")) and not list(srv.tmp.rglob("evil*"))


def test_invalid_torrent_is_refused():
    with running_server(SETTINGS) as srv:
        st, out = post(srv, "bad.torrent", b"not a torrent")
        assert st == 400 and "not a valid torrent" in out["error"]
        assert srv.manager.all_links() == []


def test_link_list_and_container_files_are_read():
    with running_server() as srv:
        st, out = post(srv, "links.txt", b"https://example.com/a.zip\nhttps://example.com/b.zip\n")
        assert st == 200 and out["kind"] == "links" and out["links"] == 2, out
        st, out = post(srv, "job.crawljob", b"text=https://example.com/c.zip\npackageName=Pack\n")
        assert st == 200 and out["links"] == 1, out
        st, out = post(srv, "shortcut.url", b"[InternetShortcut]\nURL=https://example.com/d.zip\n")
        assert st == 200 and out["links"] == 1, out
        st, out = post(srv, "x.dlc", b"garbage")
        assert st == 400 and "x.dlc" in out["error"]


def test_unknown_extension_is_refused_with_a_clear_message():
    with running_server() as srv:
        st, out = post(srv, "setup.exe", b"MZ")
        assert st == 415 and out["error"] == "OpenGrab can't open .exe files"
        st, out = post(srv, "noext", b"x")
        assert st == 415


def test_too_large_body_is_refused_without_reading_it():
    with running_server() as srv:
        st, out = post(srv, "big.torrent", b"d", length=32 * 1024 * 1024 + 1)
        assert st == 413


def test_requires_auth():
    with running_server() as srv:
        st, out = post(srv, "a.txt", b"https://example.com/a.zip", auth=False)
        assert st == 401
        assert srv.manager.all_links() == []


def test_ui_hooks_exist():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "dataTransfer?.files" in js and "/api/import-file" in js and "file.arrayBuffer()" in js
    assert 'Authorization"] = "Bearer "' in js and "withBasePath(\"/api/import-file\")" in js
    assert re.search(r'id="tb-open"[^>]*aria-label="Open file…"[^>]*title|id="tb-open"[^>]*title="[^"]+" aria-label="Open file…"', html)
    assert re.search(r'<input type="file" id="open-file-input" multiple hidden accept="\.torrent,\.dlc,\.ccf,\.rsdf,\.crawljob,\.txt,\.url"', html)
    assert 'id="add-open-file"' in html
    assert '$("#tb-open")' in js and '$("#add-open-file")' in js and "importFiles(files)" in js
    import json
    zh = json.loads((STATIC / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    assert "Open file…" in zh["strings"] and "release to open files" in zh["strings"]
    assert (STATIC / "i18n" / "zh-CN.json").read_bytes().endswith(b"\n")
