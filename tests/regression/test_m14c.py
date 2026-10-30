"""M14c: JDownloader 2 import, dashboard statistics, Windows link handlers
and autostart, command-line targets."""
import json
import sys
import time
import uuid
from urllib.parse import quote
import zipfile
from pathlib import Path

import pytest

from opengrab import cli, jd2import
from opengrab import winintegration as wi
from opengrab.engine.stats import DailyStats
from tests.helpers.apiserver import req, pair_device, running_server
from tests.helpers.filesite import FileSite


def _wait(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


# --------------------------------------------------------------------- JD2 --
def _zip(path: Path, entries: dict) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, json.dumps(data))
        z.writestr("extraInfo", json.dumps({"x": 1}))  # non-numbered entries are ignored


def make_jd2(root: Path, base: str = "http://127.0.0.1:9/") -> Path:
    """A JD2 folder shaped like a real install (structure checked against
    JDownloader 2 on the developer's machine)."""
    cfg = root / "cfg"
    cfg.mkdir(parents=True)
    _zip(cfg / "downloadList3.zip", {"0": {"name": "Old"}, "0_0": {"url": base + "old.bin"}})
    _zip(cfg / "downloadList12.zip", {
        "0": {"name": "Holiday photos", "downloadFolder": "C:\\x"},
        "0_0": {"url": "directhttp://" + base + "a.zip", "name": "a.zip", "size": 1000,
                "properties": {"URL_ORIGIN": base + "a.zip", "FINAL_FILENAME": "a-final.zip"}},
        "0_1": {"url": base + "b.zip", "name": "b.zip", "size": 2000, "finalLinkState": "FINISHED"},
        "1": {"name": "Linux"},
        "1_0": {"url": base + "iso.iso", "name": "iso.iso", "properties": {"VERIFIEDFILESIZE": 5}},
    })
    _zip(cfg / "linkcollector5.zip", {
        "0": {"name": "Review me"},
        "0_0": {"downloadLink": {"url": base + "c.rar", "name": "c.rar", "size": 7}},
        "0_1": {"sourceUrls": [base + "d.rar"], "downloadLink": {}},
    })
    (cfg / jd2import.GENERAL).write_text(json.dumps({
        "defaultdownloadfolder": str(root / "jd-downloads"), "maxsimultanedownloads": 5,
        "maxchunksperfile": 4, "downloadspeedlimitenabled": True, "downloadspeedlimit": 2048 * 1024,
        "maxdownloadsperhostenabled": True, "maxsimultanedownloadsperhost": 2}), encoding="utf-8")
    (cfg / "org.jdownloader.settings.AccountSettings.accounts.ejs").write_bytes(b"\x00secret")
    return root


def test_jd2_reader_uses_the_newest_lists_and_maps_settings(tmp_path):
    exp = jd2import.read_export(make_jd2(tmp_path / "JDownloader 2"))
    urls = [l.url for l in exp.downloads]
    assert urls == ["http://127.0.0.1:9/a.zip", "http://127.0.0.1:9/b.zip", "http://127.0.0.1:9/iso.iso"]
    a, b, iso = exp.downloads
    assert (a.name, a.size, a.package, a.finished) == ("a-final.zip", 1000, "Holiday photos", False)
    assert b.finished and iso.size == 5 and iso.package == "Linux"
    assert [l.url for l in exp.linkgrabber] == ["http://127.0.0.1:9/c.rar", "http://127.0.0.1:9/d.rar"]
    assert exp.settings == {"download_dir": str(tmp_path / "JDownloader 2" / "jd-downloads"),
                            "max_parallel": 5, "per_link_chunks": 4, "speed_limit_kbps": 2048,
                            "max_per_host": 2}
    assert any("encrypted" in e for e in exp.errors)
    # the cfg folder itself also works, and a wrong folder is an error
    assert len(jd2import.read_export(tmp_path / "JDownloader 2" / "cfg").downloads) == 3
    assert jd2import.read_export(tmp_path / "nothing").errors == ["no cfg folder found"]


def test_jd2_files_are_not_changed(tmp_path):
    root = make_jd2(tmp_path / "jd")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    jd2import.read_export(root)
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_jd2_import_api(tmp_path):
    root = make_jd2(tmp_path / "JDownloader 2")
    with running_server() as srv:
        st, found = req(srv, "GET", f"/api/import/jd2?path={quote(str(root))}")
        assert st == 200
        f = found["found"][0]
        assert (f["downloads"], f["unfinished"], f["linkgrabber"]) == (3, 2, 2)
        assert "Holiday photos" in f["packages"]

        st, res = req(srv, "POST", "/api/import/jd2", body={"path": str(root)})
        assert st == 200, res
        assert res["downloads"] == 2 and res["linkgrabber"] == 2 and res["skipped"] == 1
        assert set(res["settings"]) == {"download_dir", "max_parallel", "per_link_chunks",
                                        "speed_limit_kbps", "max_per_host"}
        assert srv.manager.settings.max_parallel == 5 and srv.manager.settings.per_link_chunks == 4
        links = {l.url: l for l in srv.manager.all_links()}
        a = links["http://127.0.0.1:9/a.zip"]
        assert a.extra["package"] == "Holiday photos" and a.extra["source"] == "jd2"
        assert a.filename == "a-final.zip"
        assert "http://127.0.0.1:9/b.zip" not in links  # finished in JD2
        cands = list(srv._clipboard_candidates.values())
        assert {c["url"] for c in cands} == {"http://127.0.0.1:9/c.rar", "http://127.0.0.1:9/d.rar"}
        assert all(c["source"] == "jd2" and c["package"] == "Review me" for c in cands)

        # importing again adds nothing twice
        st, again = req(srv, "POST", "/api/import/jd2",
                        body={"path": str(root), "settings": False, "finished": True})
        assert again["downloads"] == 1 and again["linkgrabber"] == 0  # only b.zip is new

        # a paired phone cannot read local folders or change settings this way
        dev = pair_device(srv)
        assert req(srv, "GET", f"/api/import/jd2?path={quote(str(root))}", auth=dev)[0] == 403
        assert req(srv, "POST", "/api/import/jd2", body={"path": str(root)}, auth=dev)[0] == 403


def test_jd2_import_of_an_empty_folder_is_an_error(tmp_path):
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/import/jd2", body={"path": str(tmp_path)})
        assert st == 400 and "cfg" in res["error"]


# ---------------------------------------------------------------- dashboard --
class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_daily_stats_totals_retention_and_reload(tmp_path):
    clock = Clock(time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1)))
    s = DailyStats(tmp_path / "stats.json", clock=clock)
    s.add_bytes(1000, "a.example")
    s.add_bytes(500, "b.example")
    s.add_bytes(0, "ignored")
    s.count("finished")
    s.count("failed")
    clock.t += 86400
    s.add_bytes(300, "a.example")
    s.save()
    again = DailyStats(tmp_path / "stats.json", clock=clock)
    summ = again.summary(14)
    assert len(summ["days"]) == 14 and summ["days"][-1]["day"] == "2026-10-02"
    assert summ["today"]["bytes"] == 300
    assert summ["days"][-2] == {"day": "2026-10-01", "bytes": 1500, "finished": 1, "failed": 1}
    assert summ["top_hosts"][0] == {"host": "a.example", "bytes": 1300}
    assert summ["total_bytes"] == 1800
    clock.t += 100 * 86400  # older than 90 days is dropped on save
    again.add_bytes(1)
    again.save()
    assert list(DailyStats(tmp_path / "stats.json", clock=clock).days) == [again._today()]


def test_dashboard_counts_a_real_download(tmp_path):
    site = FileSite()
    try:
        data = b"x" * 300_000
        url = site.add("/d/file.bin", data)
        with running_server() as srv:
            link = srv.manager.add_url(url)
            _wait(lambda: srv.manager.get(link.id).status.value == "finished")
            # the status flips a moment before the "finished" event is counted
            _wait(lambda: req(srv, "GET", "/api/dashboard")[1]["today"]["finished"] == 1)
            st, dash = req(srv, "GET", "/api/dashboard")
            assert st == 200
            assert dash["today"]["bytes"] == len(data)
            assert dash["today"]["finished"] == 1
            assert dash["top_hosts"][0]["host"] == "127.0.0.1"
            assert dash["counts"]["finished"] == 1
            assert dash["tiles"] == ["today", "speed", "active", "days", "sites"]
            # tile order is a normal setting
            st, res = req(srv, "POST", "/api/settings", body={"dashboard_tiles": ["sites", "today"]})
            assert "dashboard_tiles" in res["applied"]
            assert req(srv, "GET", "/api/dashboard")[1]["tiles"] == ["sites", "today"]
            # samples accumulate for the speed graph
            _wait(lambda: req(srv, "GET", "/api/dashboard")[1]["speed_history"], timeout=6)
            srv.features["dashboard"].shutdown()
            saved = json.loads((Path(srv.manager.data_dir) / "stats.json").read_text())
            assert sum(d["bytes"] for d in saved["days"].values()) == len(data)
    finally:
        site.close()


# ----------------------------------------------------------- link handlers --
def test_normalize_and_split_targets(tmp_path):
    enc = "opengrab://add?url=https%3A%2F%2Fhost.example%2Ff.zip%3Fa%3D1"
    assert wi.normalize_target(enc) == "https://host.example/f.zip?a=1"
    assert wi.normalize_target("opengrab://https://host.example/x") == "https://host.example/x"
    assert wi.normalize_target("opengrab:https:/host.example/x") == "https://host.example/x"
    assert wi.normalize_target("opengrab://add?url=file%3A%2F%2Fc%2Fx") == ""  # only web links
    assert wi.normalize_target("opengrab://javascript:alert(1)") == ""
    assert wi.normalize_target('"magnet:?xt=urn:btih:abc"') == "magnet:?xt=urn:btih:abc"
    f = tmp_path / "list.crawljob"
    urls, files = wi.split_targets(["magnet:?xt=urn:btih:abc", str(f), "opengrab://http://h/x", ""])
    assert urls == ["magnet:?xt=urn:btih:abc", "http://h/x"] and files == [str(f.resolve())]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_registry_handlers_and_autostart_in_a_scratch_key():
    import winreg
    base = rf"Software\OpenGrabTest\{uuid.uuid4().hex}"
    reg = wi.Registry(base=base, command='"C:\\OpenGrab\\OpenGrab.exe"')
    try:
        assert reg.status()["protocols"] == {"magnet": False, "opengrab": False}
        reg.set_protocol("magnet", True)
        reg.set_file_type(".torrent", True)
        reg.set_autostart(True)
        st = reg.status()
        assert st["protocols"]["magnet"] and st["files"][".torrent"] and st["autostart"]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\Software\Classes\magnet\shell\open\command") as k:
            assert winreg.QueryValueEx(k, "")[0] == '"C:\\OpenGrab\\OpenGrab.exe" "%1"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\" + wi.RUN_KEY) as k:
            assert winreg.QueryValueEx(k, "OpenGrab")[0].endswith("--minimized")
        reg.set_protocol("magnet", False)
        reg.set_file_type(".torrent", False)
        reg.set_autostart(False)
        st = reg.status()
        assert not st["protocols"]["magnet"] and not st["files"][".torrent"] and not st["autostart"]

        # another program's handler is left alone when OpenGrab's is switched off
        other = wi.Registry(base=base, command='"C:\\Other\\torrent.exe"')
        other.set_protocol("magnet", True)
        other.set_file_type(".torrent", True)
        reg.set_protocol("magnet", False)
        reg.set_file_type(".torrent", False)
        assert other.protocol_is_ours("magnet") and other._get(r"Software\Classes\.torrent") != wi.PROGID
    finally:
        wi.Registry(base="")._delete_tree(base)
    with pytest.raises(OSError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)


def test_open_route_handles_links_files_and_refuses_devices(tmp_path):
    job = tmp_path / "from-browser.crawljob"
    job.write_text("text=http://127.0.0.1:9/one.bin http://127.0.0.1:9/two.bin\npackageName=Pack\n")
    dlc = tmp_path / "x.dlc"
    dlc.write_bytes(b"not really")
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/open", body={"targets": [
            "opengrab://add?url=http%3A%2F%2F127.0.0.1%3A9%2Fdirect.bin", str(job), str(dlc),
            str(tmp_path / "missing.torrent")]})
        assert st == 200, res
        assert res["files"] == 1 and res["links"] == 3
        assert any("x.dlc" in e for e in res["errors"]) and any("missing.torrent" in e for e in res["errors"])
        assert "http://127.0.0.1:9/direct.bin" in {l.url for l in srv.manager.all_links()}
        cands = {c["url"]: c for c in srv._clipboard_candidates.values()}
        assert cands["http://127.0.0.1:9/one.bin"]["package"] == "Pack"
        dev = pair_device(srv)
        assert req(srv, "POST", "/api/open", body={"targets": [str(job)]}, auth=dev)[0] == 403
        assert req(srv, "POST", "/api/system/integration", body={"autostart": True}, auth=dev)[0] == 403
        st, integ = req(srv, "GET", "/api/system/integration")
        assert st == 200 and integ["available"] == (sys.platform == "win32")


def test_second_launch_forwards_links_and_files(tmp_path):
    from opengrab.single_instance import forward_to_running
    job = tmp_path / "a.crawljob"
    job.write_text("text=http://127.0.0.1:9/fwd.bin\n")
    with running_server() as srv:
        ok = forward_to_running(["http://127.0.0.1:9/link.bin"], "127.0.0.1", srv.port_used,
                                srv.manager.settings.api_token, "", files=[str(job)])
        assert ok
        assert "http://127.0.0.1:9/link.bin" in {l.url for l in srv.manager.all_links()}
        assert "http://127.0.0.1:9/fwd.bin" in {c["url"] for c in srv._clipboard_candidates.values()}


def test_cli_positional_targets_reach_the_gui(monkeypatch, tmp_path):
    from opengrab import app
    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path))
    seen = {}

    def fake_gui(settings, host=None, port=None, urls=(), files=(), minimized=False):
        seen.update(urls=list(urls), files=list(files), minimized=minimized)

    monkeypatch.setattr(app, "run_gui_mode", fake_gui)
    monkeypatch.setattr(cli, "setup_logging", lambda: None, raising=False)
    job = tmp_path / "x.torrent"
    assert cli.main(["--minimized", "magnet:?xt=urn:btih:abc", str(job),
                     "opengrab://add?url=https%3A%2F%2Fh.example%2Fz"]) == 0
    assert seen == {"urls": ["magnet:?xt=urn:btih:abc", "https://h.example/z"],
                    "files": [str(job.resolve())], "minimized": True}
