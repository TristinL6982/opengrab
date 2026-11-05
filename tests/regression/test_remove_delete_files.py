"""Removing a download can also delete its files from this PC (safely)."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from helpers.apiserver import req, running_server  # noqa: E402
from opengrab import filedelete  # noqa: E402
from opengrab.models import Link, LinkStatus  # noqa: E402

STATIC = ROOT / "src" / "opengrab" / "ui" / "static"


@pytest.fixture(autouse=True)
def _no_recycle_bin(monkeypatch):
    # never touch the real Recycle Bin from tests
    monkeypatch.setattr(filedelete, "USE_RECYCLE_BIN", False)


def _finished(srv, name, content=b"data", **extra):
    dl = Path(srv.settings.download_dir)
    f = dl / name
    f.write_bytes(content)
    link = Link(url="https://example.com/" + name, filename=name, status=LinkStatus.FINISHED,
                path=str(f), **extra)
    srv.manager._links[link.id] = link
    return link, f


def test_remove_from_list_keeps_the_file():
    with running_server() as srv:
        link, f = _finished(srv, "keep.bin")
        code, body = req(srv, "POST", f"/api/links/{link.id}/remove", {})
        assert code == 200 and body["deleted"] == [] and body["failed"] == []
        assert f.exists()
        assert srv.manager.get(link.id) is None


def test_delete_files_flag_removes_file_and_part_files():
    with running_server() as srv:
        link, f = _finished(srv, "gone.bin")
        part = Path(str(f) + ".part")
        meta = Path(str(f) + ".part.json")
        part.write_bytes(b"x")
        meta.write_text("{}")
        other = f.parent / "other.bin"
        other.write_bytes(b"keep me")
        code, body = req(srv, "POST", f"/api/links/{link.id}/remove", {"delete_files": True})
        assert code == 200
        assert not f.exists() and not part.exists() and not meta.exists()
        assert other.exists()
        assert len(body["deleted"]) == 3 and body["failed"] == []


def test_unfinished_link_part_files_are_deleted():
    with running_server() as srv:
        dl = Path(srv.settings.download_dir)
        part = dl / "half.bin.part"
        part.write_bytes(b"xx")
        link = Link(url="https://example.com/half.bin", filename="half.bin", status=LinkStatus.PAUSED)
        srv.manager._links[link.id] = link
        # a finished file with the same name belongs to someone else and stays
        stranger = dl / "half.bin"
        stranger.write_bytes(b"not ours")
        code, body = req(srv, "POST", f"/api/links/{link.id}/remove", {"delete_files": True})
        assert code == 200 and not part.exists() and stranger.exists()


def test_path_outside_download_dir_is_refused(tmp_path):
    with running_server() as srv:
        outside = tmp_path / "precious.txt"
        outside.write_text("do not delete")
        link = Link(url="https://example.com/p", filename="precious.txt",
                    status=LinkStatus.FINISHED, path=str(outside))
        srv.manager._links[link.id] = link
        code, body = req(srv, "POST", f"/api/links/{link.id}/remove", {"delete_files": True})
        assert code == 200
        assert outside.exists()
        assert body["deleted"] == [] and body["failed"][0]["path"] == str(outside)


def test_dotdot_and_directories_are_refused(tmp_path):
    root = tmp_path / "dl"
    (root / "sub").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    res = filedelete.delete_files([root / "sub" / ".." / ".." / "secret.txt", root / "sub"], [root])
    assert secret.exists() and (root / "sub").is_dir()
    assert res["deleted"] == [] and len(res["failed"]) == 2


def test_delete_files_without_flag_value_true_does_nothing_extra():
    with running_server() as srv:
        link, f = _finished(srv, "str.bin")
        req(srv, "POST", f"/api/links/{link.id}/remove", {"delete_files": "yes"})
        assert f.exists()


def test_several_links_can_be_removed_with_files():
    with running_server() as srv:
        pairs = [_finished(srv, f"m{i}.bin") for i in range(3)]
        for link, _ in pairs:
            req(srv, "POST", f"/api/links/{link.id}/remove", {"delete_files": True})
        assert not any(f.exists() for _, f in pairs)


def test_remove_dialog_markup_and_wiring():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '<dialog id="remove-dialog" class="win" aria-labelledby="remove-title">' in html
    for ident in ("remove-list", "remove-files", "remove-cancel"):
        assert f'id="{ident}"' in html
    assert "Remove from list" in html and "Remove and delete files from this PC" in html
    # toolbar button, Delete key and the row action all go through the dialog
    assert 'ev.key === "Delete"' in js and "removeSelected()" in js
    assert "askRemove(" in js and "delete_files: true" in js
    assert "confirm(\"Remove this link?\")" not in js
