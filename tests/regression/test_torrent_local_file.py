"""A local .torrent added as a file:// URL (drag and drop) loads as a torrent file."""
import os
import time
from pathlib import Path

import pytest

from tests.helpers.apiserver import req, running_server

lt = pytest.importorskip("libtorrent")

SETTINGS = {"torrent": {"dht": False, "lsd": False, "pex": False}, "torrent_listen_port": 0,
            "torrent_interface": "127.0.0.1", "trackers": {"trackers": []}}


def _make_torrent(folder: Path, name="Some Name [Tag].torrent") -> Path:
    content = folder / "content" / "bundle"
    content.mkdir(parents=True)
    (content / "a.bin").write_bytes(os.urandom(200 * 1024))
    fs = lt.file_storage()
    lt.add_files(fs, str(content))
    ct = lt.create_torrent(fs, piece_size=64 * 1024)
    lt.set_piece_hashes(ct, str(content.parent))
    out = folder / name
    out.write_bytes(lt.bencode(ct.generate()))
    return out


def _wait(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out")


def test_file_url_with_spaces_and_brackets_loads_the_torrent_file(tmp_path):
    folder = tmp_path / "My Torrents [new]"
    folder.mkdir()
    tf = _make_torrent(folder)
    url = tf.as_uri()
    assert "%20" in url and "%5B" in url.upper()
    with running_server(SETTINGS) as srv:
        st, out = req(srv, "POST", "/api/urls", body={"urls": [url]})
        assert not out[0].get("error"), out
        lid = out[0]["id"]
        link = _wait(lambda: (srv.manager.get(lid).extra.get("torrent") or {}).get("info_hash")
                     and srv.manager.get(lid))
        assert link.status.value != "error", link.error
        assert link.plugin == "torrent"
        t = link.extra["torrent"]
        copy = Path(t["torrent_file"])
        assert copy.is_file() and copy.parent.name == "torrents"  # private copy in the data dir
        tf.unlink()  # the original may go away; the copy is what restore uses
        assert copy.is_file()


def test_windows_style_path_loads_the_torrent_file(tmp_path):
    folder = tmp_path / "plain dir"
    folder.mkdir()
    tf = _make_torrent(folder, "x.TORRENT")
    with running_server(SETTINGS) as srv:
        link = srv.manager.add_url(str(tf))
        _wait(lambda: (srv.manager.get(link.id).extra.get("torrent") or {}).get("info_hash"))
        assert srv.manager.get(link.id).status.value != "error"


def test_missing_file_gives_a_clear_error(tmp_path):
    url = (tmp_path / "gone [x]" / "nope.torrent").as_uri()
    with running_server(SETTINGS) as srv:
        with pytest.raises(ValueError, match="not found"):
            srv.manager.add_url(url)
        st, out = req(srv, "POST", "/api/urls", body={"urls": [url]})
        assert "not found" in out[0]["error"]
        assert srv.manager.all_links() == []


def test_invalid_torrent_file_is_refused(tmp_path):
    bad = tmp_path / "bad.torrent"
    bad.write_bytes(b"this is not bencode")
    with running_server(SETTINGS) as srv:
        with pytest.raises(ValueError, match="not a valid torrent"):
            srv.manager.add_url(bad.as_uri())


def test_file_url_to_anything_else_is_refused(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("hunter2")
    with running_server(SETTINGS) as srv:
        for url in (secret.as_uri(), secret.as_uri() + "?x=.torrent", "file:///etc/passwd"):
            with pytest.raises(ValueError, match="only local .torrent"):
                srv.manager.add_url(url)
        st, out = req(srv, "POST", "/api/urls", body={"urls": [secret.as_uri()]})
        assert "error" in out[0]
        assert srv.manager.all_links() == []
