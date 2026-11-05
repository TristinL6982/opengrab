"""A "Save to" folder for adds, recent folders, the folder picker route, the
ask-before setting and Change folder (queued, partial and finished links)."""
import os
import re
import time
from pathlib import Path

import pytest

from opengrab import filedelete
from opengrab.models import Link, LinkStatus
from opengrab.savedir import MAX_RECENT, SaveDirError, validate_save_dir
from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite

STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"
BLOB = os.urandom(120 * 1024)


@pytest.fixture(autouse=True)
def _no_recycle_bin(monkeypatch):
    monkeypatch.setattr(filedelete, "USE_RECYCLE_BIN", False)


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


def _wait(pred, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _done(srv, lid):
    return srv.manager.get(lid).status == LinkStatus.FINISHED


def test_add_with_save_dir_puts_the_file_there(site, tmp_path):
    url = site.add("/one.bin", BLOB)
    target = tmp_path / "elsewhere"
    target.mkdir()
    with running_server({"auto_extract_archives": False}) as srv:
        st, out = req(srv, "POST", "/api/urls", {"urls": [url], "save_dir": str(target)})
        assert st == 200 and not out[0].get("error"), out
        lid = out[0]["id"]
        _wait(lambda: _done(srv, lid))
        link = srv.manager.get(lid)
        assert Path(link.path).parent == target.resolve()
        assert (target / "one.bin").read_bytes() == BLOB
        assert not (Path(srv.settings.download_dir) / "one.bin").exists()
        assert link.extra["save_dir"] == str(target)


def test_bulk_add_applies_one_folder_to_every_link(site, tmp_path):
    urls = [site.add(f"/bulk/{i}.bin", BLOB[:2000 + i]) for i in range(32)]
    target = tmp_path / "bulk"
    with running_server({"auto_extract_archives": False, "max_parallel": 8}) as srv:
        st, out = req(srv, "POST", "/api/urls",
                      {"urls": urls, "save_dir": str(target), "create_dir": True})
        assert st == 200 and len(out) == 32 and not any(o.get("error") for o in out), out[:2]
        assert target.is_dir()
        ids = [o["id"] for o in out]
        _wait(lambda: all(_done(srv, i) for i in ids), timeout=60)
        assert {Path(srv.manager.get(i).path).parent for i in ids} == {target.resolve()}
        assert len(list(target.glob("*.bin"))) == 32


def test_category_subfolders_stay_inside_the_chosen_folder(site, tmp_path):
    url = site.add("/m/clip.mp4", BLOB)
    target = tmp_path / "vids"
    target.mkdir()
    with running_server({"categories_enabled": True, "auto_extract_archives": False}) as srv:
        st, out = req(srv, "POST", "/api/urls", {"urls": [url], "save_dir": str(target)})
        lid = out[0]["id"]
        _wait(lambda: _done(srv, lid))
        assert Path(srv.manager.get(lid).path).parent == target.resolve() / "Video"


def test_recent_folders_are_recorded_deduped_and_capped(tmp_path):
    with running_server() as srv:
        folders = []
        for i in range(MAX_RECENT + 3):
            d = tmp_path / f"f{i}"
            d.mkdir()
            folders.append(d)
            st, _ = req(srv, "POST", "/api/urls", {"urls": [], "save_dir": str(d)})
            assert st == 200
        recent = srv.settings.recent_download_dirs
        assert len(recent) == MAX_RECENT
        assert recent[0] == str(folders[-1])
        # using an older one again moves it to the front without a duplicate
        req(srv, "POST", "/api/urls", {"urls": [], "save_dir": str(folders[-3])})
        recent = srv.settings.recent_download_dirs
        assert recent[0] == str(folders[-3]) and len(set(map(str.casefold, recent))) == len(recent)
        st, s = req(srv, "GET", "/api/settings")
        assert s["recent_download_dirs"] == recent


def test_relative_missing_and_unusable_paths_are_refused(tmp_path):
    with running_server() as srv:
        st, out = req(srv, "POST", "/api/urls", {"urls": ["https://example.com/a.bin"], "save_dir": "downloads/x"})
        assert st == 400 and out["code"] == "relative"
        st, out = req(srv, "POST", "/api/urls", {"urls": ["https://example.com/a.bin"], "save_dir": "~/x"})
        assert st == 400 and out["code"] == "relative"
        missing = tmp_path / "nope" / "deeper"
        st, out = req(srv, "POST", "/api/urls", {"urls": ["https://example.com/a.bin"], "save_dir": str(missing)})
        assert st == 409 and out["code"] == "missing" and not missing.exists()
        assert srv.manager.all_links() == []  # nothing was queued
        f = tmp_path / "file.txt"
        f.write_text("x")
        st, out = req(srv, "POST", "/api/check-folder", {"path": str(f)})
        assert st == 400 and out["code"] == "not_dir"
        st, out = req(srv, "POST", "/api/check-folder", {"path": str(missing), "create_dir": True})
        assert st == 200 and missing.is_dir()
        assert srv.settings.recent_download_dirs == []  # a mere check is not a use


def test_unwritable_folder_is_refused(tmp_path, monkeypatch):
    import tempfile

    def deny(*_a, **_k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", deny)
    with pytest.raises(SaveDirError) as e:
        validate_save_dir(str(tmp_path))
    assert e.value.code == "not_writable" and "cannot write" in str(e.value)


def test_default_folder_is_not_pinned_to_the_link(site):
    url = site.add("/d.bin", BLOB)
    with running_server({"auto_extract_archives": False}) as srv:
        st, out = req(srv, "POST", "/api/urls", {"urls": [url], "save_dir": srv.settings.download_dir})
        assert "save_dir" not in srv.manager.get(out[0]["id"]).extra


def test_linkgrabber_add_and_add_all_take_a_folder(tmp_path):
    target = tmp_path / "lg"
    target.mkdir()
    with running_server({"linkgrabber_online_check": False}) as srv:
        srv.manager.add_url = lambda url, **kw: _fake_add(srv, url, **kw)
        for i in range(3):
            srv._clipboard_candidates[f"{i:02x}"] = {"id": f"{i:02x}", "url": f"https://example.com/{i}.bin",
                                                     "state": "online", "source": "paste"}
        st, out = req(srv, "POST", "/api/linkgrabber/00/add", {"save_dir": str(tmp_path / "gone")})
        assert st == 409 and "00" in srv._clipboard_candidates  # kept for another try
        st, out = req(srv, "POST", "/api/linkgrabber/00/add", {"save_dir": str(target)})
        assert st == 201 and out["link"]["extra"]["save_dir"] == str(target)
        st, out = req(srv, "POST", "/api/linkgrabber/add-all", {"only_online": True, "save_dir": str(target)})
        assert st == 200 and out["added"] == 2
        assert {l.extra.get("save_dir") for l in srv.manager.all_links()} == {str(target)}


def _fake_add(srv, url, save_dir="", **_kw):
    link = Link(url=url, status=LinkStatus.PAUSED, extra={"save_dir": save_dir} if save_dir else {})
    srv.manager._links[link.id] = link
    return link


def test_folder_picker_route_is_mocked_and_falls_back(tmp_path):
    with running_server() as srv:
        st, out = req(srv, "POST", "/api/pick-folder", {})
        assert st == 200 and out == {"available": False, "path": ""}  # headless: type a path
        seen = []
        srv.folder_picker = lambda start: seen.append(start) or str(tmp_path)
        st, out = req(srv, "POST", "/api/pick-folder", {"start": "C:\\x"})
        assert out == {"available": True, "path": str(tmp_path)} and seen == ["C:\\x"]
        srv.folder_picker = lambda start: ""  # cancelled
        assert req(srv, "POST", "/api/pick-folder", {})[1]["path"] == ""


def test_ask_before_adding_setting_round_trips_and_the_hooks_exist():
    with running_server() as srv:
        assert req(srv, "GET", "/api/settings")[1]["ask_save_folder"] is False
        req(srv, "POST", "/api/settings", {"ask_save_folder": True})
        assert req(srv, "GET", "/api/settings")[1]["ask_save_folder"] is True
        assert srv.settings.ask_save_folder is True
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for el in ("url-folder", "url-browse", "url-recent", "folder-dialog", "folder-input", "folder-browse",
               "folder-recent", "folder-move", "folder-ok", "set-ask_save_folder", "tb-folder"):
        assert f'id="{el}"' in html, el
    assert "Ask where to save before adding downloads" in html
    assert '"ask_save_folder"' in js
    # every add path from the window asks once per batch
    assert len(re.findall(r"askSaveFolder\(", js)) >= 4  # definition + drop + candidate add + add all online
    assert "/api/pick-folder" in js and "/api/change-folder" in js
    assert 'data-act="change-folder"' in js or '"change-folder"' in js


def _paused(srv, name, with_part=True, **extra):
    dl = Path(srv.settings.download_dir)
    link = Link(url="https://example.com/" + name, filename=name, status=LinkStatus.PAUSED, downloaded=2,
                extra={"download_target_filename": name, **extra})
    if with_part:
        (dl / (name + ".part")).write_bytes(b"xx")
        (dl / (name + ".part.json")).write_text("{}")
    srv.manager._links[link.id] = link
    return link, dl


def test_change_folder_of_a_queued_link(site, tmp_path):
    url = site.add("/q.bin", BLOB)
    target = tmp_path / "queued"
    with running_server({"auto_extract_archives": False}) as srv:
        link = srv.manager.add_url(url, auto_download=False)  # grabbed and queued, not started
        _wait(lambda: srv.manager.get(link.id).status != LinkStatus.RESOLVING)
        st, out = req(srv, "POST", "/api/change-folder",
                      {"ids": [link.id], "save_dir": str(target), "create_dir": True})
        assert st == 200 and out["ok"] and out["results"][0]["ok"], out
        assert srv.manager.get(link.id).extra["save_dir"] == str(target.resolve())
        assert str(target) in srv.settings.recent_download_dirs[0]
        req(srv, "POST", f"/api/links/{link.id}/start")
        _wait(lambda: _done(srv, link.id))
        assert (target / "q.bin").read_bytes() == BLOB


def test_change_folder_of_several_links_at_once(tmp_path):
    target = tmp_path / "many"
    target.mkdir()
    with running_server() as srv:
        links = [Link(url=f"https://example.com/{i}", status=LinkStatus.PAUSED) for i in range(4)]
        for l in links:
            srv.manager._links[l.id] = l
        st, out = req(srv, "POST", "/api/change-folder", {"ids": [l.id for l in links], "save_dir": str(target)})
        assert [r["ok"] for r in out["results"]] == [True] * 4
        assert {srv.manager.get(l.id).extra["save_dir"] for l in links} == {str(target.resolve())}
        st, out = req(srv, "POST", "/api/change-folder", {"ids": [links[0].id], "save_dir": "relative"})
        assert st == 400 and out["code"] == "relative"


def test_partial_files_move_with_a_paused_link(tmp_path):
    target = tmp_path / "moved"
    with running_server() as srv:
        link, dl = _paused(srv, "half.bin")
        stranger = dl / "other.bin"
        stranger.write_bytes(b"not ours")
        # started links need an explicit choice, nothing moves without it
        st, out = req(srv, "POST", "/api/change-folder", {"ids": [link.id], "save_dir": str(target), "create_dir": True})
        assert out["results"][0].get("needs_choice") and (dl / "half.bin.part").exists()
        st, out = req(srv, "POST", "/api/change-folder",
                      {"ids": [link.id], "save_dir": str(target), "move_files": True})
        assert out["results"][0]["ok"], out
        assert (target / "half.bin.part").read_bytes() == b"xx" and (target / "half.bin.part.json").exists()
        assert not (dl / "half.bin.part").exists() and not (dl / "half.bin.part.json").exists()
        assert stranger.exists()  # only the link's own files move


def test_finished_file_moves_and_the_path_follows(tmp_path):
    target = tmp_path / "done"
    with running_server() as srv:
        dl = Path(srv.settings.download_dir)
        f = dl / "fin.bin"
        f.write_bytes(b"data")
        link = Link(url="https://example.com/fin.bin", filename="fin.bin", status=LinkStatus.FINISHED, path=str(f))
        srv.manager._links[link.id] = link
        st, out = req(srv, "POST", "/api/change-folder",
                      {"ids": [link.id], "save_dir": str(target), "create_dir": True, "move_files": True})
        assert out["results"][0]["ok"], out
        assert (target / "fin.bin").read_bytes() == b"data" and not f.exists()
        assert Path(srv.manager.get(link.id).path) == (target / "fin.bin").resolve() or \
            Path(srv.manager.get(link.id).path) == target / "fin.bin"


def test_moving_never_overwrites_and_can_rename(tmp_path):
    target = tmp_path / "taken"
    target.mkdir()
    existing = target / "half.bin.part"
    existing.write_bytes(b"someone else's")
    with running_server() as srv:
        link, dl = _paused(srv, "half.bin")
        st, out = req(srv, "POST", "/api/change-folder",
                      {"ids": [link.id], "save_dir": str(target), "move_files": True})
        r = out["results"][0]
        assert r["ok"] is False and r["conflicts"] == ["half.bin.part"], r
        assert existing.read_bytes() == b"someone else's" and (dl / "half.bin.part").exists()
        assert srv.manager.get(link.id).extra.get("save_dir") is None  # nothing changed
        # asked to rename instead: the whole set gets a free name
        st, out = req(srv, "POST", "/api/change-folder",
                      {"ids": [link.id], "save_dir": str(target), "move_files": True, "rename": True})
        r = out["results"][0]
        assert r["ok"] and r["renamed"] == "half_1.bin", r
        assert (target / "half_1.bin.part").read_bytes() == b"xx" and (target / "half_1.bin.part.json").exists()
        assert existing.read_bytes() == b"someone else's"
        assert srv.manager.get(link.id).filename == "half_1.bin"


def test_a_running_download_is_paused_moved_and_resumed(site, tmp_path):
    site.delay_per_64k = 0.15
    url = site.add("/slow.bin", os.urandom(1500 * 1024))
    target = tmp_path / "live"
    with running_server({"auto_extract_archives": False}) as srv:
        link = srv.manager.add_url(url)
        _wait(lambda: srv.manager.get(link.id).status == LinkStatus.DOWNLOADING
              and srv.manager.get(link.id).downloaded > 0)
        site.delay_per_64k = 0
        st, out = req(srv, "POST", "/api/change-folder",
                      {"ids": [link.id], "save_dir": str(target), "create_dir": True, "move_files": True})
        assert out["results"][0]["ok"], out
        _wait(lambda: _done(srv, link.id), timeout=40)
        assert Path(srv.manager.get(link.id).path).parent == target.resolve()
        assert not list(Path(srv.settings.download_dir).glob("slow.bin*"))


def test_torrents_only_change_folder_before_they_start(tmp_path):
    with running_server() as srv:
        link = Link(url="magnet:?xt=urn:btih:" + "a" * 40, plugin="torrent", status=LinkStatus.PAUSED,
                    extra={"torrent": {"save_path": srv.settings.download_dir}})
        srv.manager._links[link.id] = link
        st, out = req(srv, "POST", "/api/change-folder", {"ids": [link.id], "save_dir": str(tmp_path)})
        assert out["results"][0]["ok"]  # no libtorrent handle yet: the folder is used when it starts
        assert srv.manager.get(link.id).extra["save_dir"] == str(tmp_path.resolve())
        calls = []
        srv.manager.torrents.handle_for = lambda lid: object()
        srv.manager.torrents.move_storage = lambda lid, p: calls.append(p) or True
        other = tmp_path / "second"
        st, out = req(srv, "POST", "/api/change-folder", {"ids": [link.id], "save_dir": str(other), "create_dir": True})
        assert out["results"][0]["ok"] and calls == [other.resolve()]
        srv.manager.torrents.move_storage = lambda lid, p: False
        third = tmp_path / "third"
        third.mkdir()
        st, out = req(srv, "POST", "/api/change-folder", {"ids": [link.id], "save_dir": str(third)})
        assert out["results"][0]["ok"] is False and "before it starts" in out["results"][0]["error"]
