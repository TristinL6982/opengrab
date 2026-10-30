"""W10: archive volume sets (real 7-Zip), package passwords, virus scan."""
import os
import subprocess
import time
from pathlib import Path

import pytest

from opengrab.avscan import find_defender
from opengrab.engine.archives import find_7zip
from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite

SEVEN = find_7zip()
needs_7zip = pytest.mark.skipif(not SEVEN, reason="7-Zip is not installed")


def _wait(pred, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _make_7z(tmp: Path, name: str, payload: bytes, volume_kb: int = 0, password: str = "") -> list[Path]:
    src = tmp / "src"
    src.mkdir(exist_ok=True)
    (src / "inside.bin").write_bytes(payload)
    args = [SEVEN, "a", "-mx0", str(tmp / name), str(src / "inside.bin")]
    if volume_kb:
        args.insert(2, f"-v{volume_kb}k")
    if password:
        args.insert(2, "-p" + password)
    subprocess.run(args, check=True, capture_output=True)
    return sorted(p for p in tmp.iterdir() if p.name.startswith(name))


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


@needs_7zip
def test_split_set_extracts_once_after_the_last_part(tmp_path, site):
    payload = os.urandom(250 * 1024)
    parts = _make_7z(tmp_path, "movie.7z", payload, volume_kb=100)
    assert [p.name for p in parts] == ["movie.7z.001", "movie.7z.002", "movie.7z.003"]
    urls = [site.add("/set/" + p.name, p.read_bytes()) for p in parts]
    with running_server({"max_parallel": 1}) as srv:
        links = [srv.manager.add_url(u) for u in urls]
        _wait(lambda: all(srv.manager.get(l.id).status.value == "finished" for l in links))
        _wait(lambda: all(srv.manager.get(l.id).extra.get("archive_extraction", {}).get("status") == "extracted"
                          for l in links))
        res = srv.manager.get(links[-1].id).extra["archive_extraction"]
        out = Path(res["path"])
        assert out.name == "movie" and (out / "inside.bin").read_bytes() == payload


@needs_7zip
def test_incomplete_set_waits(tmp_path, site):
    parts = _make_7z(tmp_path, "half.7z", os.urandom(250 * 1024), volume_kb=100)
    urls = [site.add("/half/" + p.name, p.read_bytes()) for p in parts[:2]]  # part 3 never arrives
    with running_server() as srv:
        links = [srv.manager.add_url(u) for u in urls]
        _wait(lambda: all(srv.manager.get(l.id).status.value == "finished" for l in links))
        for l in links:
            assert srv.manager.get(l.id).extra["archive_extraction"]["status"] == "waiting"
        assert not (Path(srv.manager.settings.download_dir) / "half").exists()


@needs_7zip
def test_clicknload_password_unlocks_the_archive(tmp_path, site):
    payload = os.urandom(20 * 1024)
    (archive,) = _make_7z(tmp_path, "locked.7z", payload, password="hunter2")
    url = site.add("/pw/locked.7z", archive.read_bytes())
    with running_server() as srv:
        lg = srv.features["linkgrabber"]
        lg.add([url], "clicknload", package="Locked", passwords=["wrong", "hunter2"])
        cid = req(srv, "GET", "/api/linkgrabber")[1][0]["id"]
        lid = req(srv, "POST", f"/api/linkgrabber/{cid}/add", body={})[1]["link"]["id"]
        l = _wait(lambda: srv.manager.get(lid).extra.get("archive_extraction") and srv.manager.get(lid))
        assert l.extra["archive_extraction"]["status"] == "extracted", l.extra["archive_extraction"]
        assert (Path(l.extra["archive_extraction"]["path"]) / "inside.bin").read_bytes() == payload


def _scan_settings(code: int, quarantine=False):
    return {"avscan": {"enabled": True, "scanner": "custom", "custom_command": f'cmd /c exit {code} "{{path}}"',
                       "infected_codes": [2], "quarantine": quarantine, "timeout_s": 30}}


@pytest.mark.skipif(os.name != "nt", reason="uses cmd.exe as the fake scanner")
def test_infected_file_is_flagged_quarantined_and_announced(site):
    url = site.add("/v/tool.exe", b"MZ fake program")
    with running_server(_scan_settings(2, quarantine=True)) as srv:
        link = srv.manager.add_url(url)
        _wait(lambda: srv.manager.get(link.id).status.value == "finished")
        l = _wait(lambda: srv.manager.get(link.id).extra.get("avscan") and srv.manager.get(link.id))
        assert l.extra["avscan"]["status"] == "infected" and l.extra["avscan"]["quarantined"] is True
        assert Path(l.path).parent.name == "quarantine" and Path(l.path).exists()
        notes = req(srv, "GET", "/api/notifications")[1]
        assert any(n["title"] == "Virus found" for n in notes)
        st, info = req(srv, "GET", "/api/avscan")
        assert info["history"][0]["status"] == "infected"


@pytest.mark.skipif(os.name != "nt", reason="uses cmd.exe as the fake scanner")
def test_clean_scan_and_scanner_test_route(site):
    url = site.add("/v/readme.txt", b"hello")
    with running_server(_scan_settings(0)) as srv:
        link = srv.manager.add_url(url)
        l = _wait(lambda: srv.manager.get(link.id).extra.get("avscan") and srv.manager.get(link.id))
        assert l.extra["avscan"]["status"] == "clean" and Path(l.path).parent.name != "quarantine"
        st, res = req(srv, "POST", "/api/avscan/test", body={})
        assert st == 200 and res["status"] == "clean"


def test_scanning_is_off_by_default(site):
    url = site.add("/v/plain.bin", b"x")
    with running_server() as srv:
        link = srv.manager.add_url(url)
        _wait(lambda: srv.manager.get(link.id).status.value == "finished")
        time.sleep(0.5)
        assert "avscan" not in srv.manager.get(link.id).extra
        assert req(srv, "POST", "/api/avscan/test", body={})[0] == 400


@pytest.mark.skipif(not find_defender(os.path.exists), reason="Windows Defender command line not found")
def test_real_windows_defender_scans_a_clean_file(tmp_path):
    from opengrab.engine.postprocess import VirusScan
    from opengrab.settings import Settings
    f = tmp_path / "clean.txt"
    f.write_text("nothing to see here")
    res = VirusScan(Settings(avscan={"enabled": True, "scanner": "defender", "timeout_s": 120})).scan(str(f))
    assert res.status in ("clean", "error"), res  # error = Defender disabled by policy on this PC
    print("Defender:", res.status, res.code)
