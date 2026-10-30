"""W11: BitTorrent end to end against a local libtorrent seeder."""
import hashlib
import os
import time
from pathlib import Path

import pytest

from tests.helpers.apiserver import req, running_server

lt = pytest.importorskip("libtorrent")


def _wait(pred, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out waiting")


@pytest.fixture
def seeder(tmp_path):
    """Seed a two-file torrent from 127.0.0.1; yields (magnet, files)."""
    root = tmp_path / "seed" / "bundle"
    root.mkdir(parents=True)
    files = {"a.bin": os.urandom(600 * 1024), "b.bin": os.urandom(300 * 1024)}
    for name, data in files.items():
        (root / name).write_bytes(data)
    fs = lt.file_storage()
    lt.add_files(fs, str(root))
    ct = lt.create_torrent(fs, piece_size=64 * 1024)
    lt.set_piece_hashes(ct, str(root.parent))
    ti = lt.torrent_info(lt.bencode(ct.generate()))
    ses = lt.session({"listen_interfaces": "127.0.0.1:0", "enable_dht": False, "enable_lsd": False,
                      "enable_upnp": False, "enable_natpmp": False,
                      "allow_multiple_connections_per_ip": True})
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = str(root.parent)
    params.flags |= lt.torrent_flags.seed_mode
    ses.add_torrent(params)
    magnet = lt.make_magnet_uri(ti) + f"&x.pe=127.0.0.1:{ses.listen_port()}"
    yield magnet, files, ti.name(), ses
    del ses


SETTINGS = {"torrent": {"dht": False, "lsd": False, "pex": False,
                        # reconnect to the lone test peer quickly after a pause
                        "advanced": {"min_reconnect_time": 1}}, "torrent_listen_port": 0,
            "torrent_interface": "127.0.0.1", "trackers": {"trackers": []}}


def test_magnet_downloads_from_a_peer_and_finishes(seeder):
    magnet, files, name, _ses = seeder
    with running_server(SETTINGS) as srv:
        st, out = req(srv, "POST", "/api/urls", body={"urls": [magnet]})
        lid = out[0]["id"]
        link = _wait(lambda: srv.manager.get(lid).status.value == "finished" and srv.manager.get(lid))
        folder = Path(link.path)
        assert folder.name == name
        for fname, data in files.items():
            assert hashlib.sha256((folder / fname).read_bytes()).digest() == hashlib.sha256(data).digest()
        st, info = req(srv, "GET", "/api/torrents")
        t = next(x for x in info["torrents"] if x["id"] == lid)
        assert info["installed"] and t["info_hash"] and t["seeding"] == "done"  # no limits: stop at 100%
        notes = req(srv, "GET", "/api/notifications")[1]
        assert any(n["kind"] == "finished" for n in notes)


def test_file_selection_pause_and_resume(seeder):
    magnet, files, name, ses = seeder
    # a slow seeder, so selection and pause happen mid-transfer (libtorrent
    # exempts local peers from limits unless told otherwise)
    ses.apply_settings({"upload_rate_limit": 60 * 1024, "ignore_limits_on_local_network": False})
    with running_server(SETTINGS) as srv:
        lid = req(srv, "POST", "/api/urls", body={"urls": [magnet]})[1][0]["id"]
        listing = _wait(lambda: req(srv, "GET", f"/api/torrents/{lid}/files")[1]["files"])
        assert sorted(f["path"].replace("\\", "/").split("/")[-1] for f in listing) == ["a.bin", "b.bin"]
        idx_b = next(f["index"] for f in listing if f["path"].endswith("b.bin"))
        st, res = req(srv, "POST", f"/api/torrents/{lid}/files", body={"selected": [idx_b]})
        assert st == 200 and res["priorities"][idx_b] == 4
        other = next(f["index"] for f in listing if f["path"].endswith("a.bin"))
        assert res["priorities"][other] == 0
        assert req(srv, "POST", f"/api/links/{lid}/pause", body={})[0] == 200
        assert srv.manager.get(lid).status.value == "paused"
        assert req(srv, "POST", f"/api/links/{lid}/resume", body={})[0] == 200
        ses.apply_settings({"upload_rate_limit": 0})
        link = _wait(lambda: srv.manager.get(lid).status.value == "finished" and srv.manager.get(lid))
        folder = Path(link.path)
        assert (folder / "b.bin").read_bytes() == files["b.bin"]
        assert link.size == len(files["b.bin"])  # only the selected file is wanted


def test_trackers_are_listed_and_editable():
    with running_server() as srv:
        st, t = req(srv, "GET", "/api/trackers")
        assert st == 200 and len(t["trackers"]) >= 3  # public defaults
        st, r = req(srv, "POST", "/api/trackers", body={"url": "udp://tracker.example.test:1337/announce"})
        assert r["added"] == 1
        assert any(x["url"].startswith("udp://tracker.example.test") for x in req(srv, "GET", "/api/trackers")[1]["trackers"])
        assert req(srv, "DELETE", "/api/trackers?url=udp://tracker.example.test:1337/announce")[0] == 200


def test_reconnect_runs_the_command_until_the_ip_changes(tmp_path):
    with running_server() as srv:
        assert req(srv, "POST", "/api/reconnect", body={})[0] == 400  # no command yet
        ips = iter(["1.1.1.1", "2.2.2.2"])
        srv.manager._public_ip = lambda raw: next(ips)
        flag = tmp_path / "ran.txt"
        cmd = f'cmd /c echo x > "{flag}"' if os.name == "nt" else f'sh -c "echo x > {flag}"'
        req(srv, "POST", "/api/settings", body={"reconnect": {"enabled": True, "command": cmd, "settle_seconds": 0}})
        st, res = req(srv, "POST", "/api/reconnect", body={})
        assert st == 200 and res["ok"] and res["old_ip"] == "1.1.1.1" and res["new_ip"] == "2.2.2.2"
        assert flag.exists()
        assert req(srv, "GET", "/api/reconnect")[1]["history"][0]["ok"] is True


def test_network_sections_are_validated():
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/settings", body={"reconnect": {"max_attempts": 0}})
        assert any("reconnect" in w for w in res["warnings"])
        assert req(srv, "GET", "/api/network")[1]["errors"] == ["reconnect"]
