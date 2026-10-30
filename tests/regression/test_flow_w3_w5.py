"""W3-W5: checksums, resume validation, categories, host limits, schedule,
speed profiles, data cap and named queues, all through the running engine."""
import hashlib
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite

BLOB = os.urandom(300 * 1024)
SHA = hashlib.sha256(BLOB).hexdigest()


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


def _wait(pred, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _status(srv, lid):
    return srv.manager.get(lid).status.value


def test_checksum_ok_and_mismatch(site):
    good = site.add("/good.bin", BLOB)
    bad = site.add("/bad.bin", BLOB)
    with running_server() as srv:
        st, out = req(srv, "POST", "/api/urls", body={"urls": [good], "hash": "sha256:" + SHA})
        a = out[0]["id"]
        _wait(lambda: _status(srv, a) == "finished")
        assert srv.manager.get(a).extra["hash"] == {"ok": True, "algo": "sha256", "expected": SHA, "actual": SHA}
        b = srv.manager.add_url(bad, expected_hash="sha256:" + "0" * 64)
        _wait(lambda: _status(srv, b.id) == "error")
        lb = srv.manager.get(b.id)
        assert lb.error.startswith("hash mismatch") and lb.extra["hash"]["ok"] is False
        assert not any(p.name == "bad.bin" for p in Path(srv.manager.settings.download_dir).rglob("*"))
        st, err = req(srv, "POST", "/api/urls", body={"urls": [good], "hash": "sha256:xyz"})
        assert "error" in err[0]
        # verify a finished file later with a pasted checksum
        st, res = req(srv, "POST", f"/api/links/{a}/verify", body={"expected": "md5:" + hashlib.md5(BLOB).hexdigest()})
        assert st == 200 and res["ok"] is True and res["algo"] == "md5"


def test_checksum_file_next_to_download(site):
    url = site.add("/pkg/tool.zip", BLOB)
    site.add("/pkg/tool.zip.sha256", f"{SHA}  tool.zip\n".encode())
    with running_server({"hash_checksum_files": True}) as srv:
        link = srv.manager.add_url(url)
        _wait(lambda: _status(srv, link.id) == "finished")
        l = srv.manager.get(link.id)
        assert l.extra["hash"]["ok"] is True and l.extra["hash_source"].endswith("tool.zip.sha256")


def test_categories_pick_the_folder_unless_packagizer_matches(site):
    v = site.add("/m/clip.mp4", BLOB)
    z = site.add("/m/pack.zip", BLOB)
    with running_server({"categories_enabled": True, "auto_extract_archives": False}) as srv:
        a = srv.manager.add_url(v)
        b = srv.manager.add_url(z)
        _wait(lambda: _status(srv, a.id) == "finished" and _status(srv, b.id) == "finished")
        root = Path(srv.manager.settings.download_dir).resolve()
        assert Path(srv.manager.get(a.id).path).parent == root / "Video"
        assert Path(srv.manager.get(b.id).path).parent == root / "Archives"
        assert srv.manager.get(a.id).extra["category"] == "Video"
    with running_server({"auto_extract_archives": False}) as srv:  # off by default
        c = srv.manager.add_url(v)
        _wait(lambda: _status(srv, c.id) == "finished")
        assert Path(srv.manager.get(c.id).path).parent == Path(srv.manager.settings.download_dir).resolve()


def test_per_host_limit_runs_one_at_a_time(site):
    site.delay_per_64k = 0.15
    urls = [site.add(f"/h/{i}.bin", BLOB) for i in range(2)]
    with running_server({"max_per_host": 1, "max_parallel": 4}) as srv:
        a, b = (srv.manager.add_url(u) for u in urls)
        _wait(lambda: "downloading" in (_status(srv, a.id), _status(srv, b.id)))
        waiting = _wait(lambda: next((srv.manager.get(x.id) for x in (a, b)
                                      if srv.manager.get(x.id).extra.get("wait_reason")), None))
        assert waiting.extra["wait_reason"] == "Waiting for a free slot on 127.0.0.1"
        assert sum(_status(srv, x.id) == "downloading" for x in (a, b)) == 1
        _wait(lambda: _status(srv, a.id) == "finished" and _status(srv, b.id) == "finished", 40)


def _window(action, speed=0):
    now = datetime.now()
    start = (now - timedelta(minutes=5)).strftime("%H:%M")
    end = (now + timedelta(minutes=30)).strftime("%H:%M")
    return {"start": start, "end": end, "action": action, "speed_kbps": speed}


def test_schedule_pause_window_holds_downloads_until_removed(site):
    url = site.add("/s/file.bin", BLOB)
    with running_server({"scheduler_enabled": True, "scheduler_windows": [_window("pause")]}) as srv:
        link = srv.manager.add_url(url)
        l = _wait(lambda: srv.manager.get(link.id).extra.get("wait_reason") and srv.manager.get(link.id))
        assert l.extra["wait_reason"] == "Paused by the schedule" and l.status.value == "waiting"
        st, flow = req(srv, "GET", "/api/flow")
        assert flow["schedule"]["action"] == "pause"
        st, res = req(srv, "POST", "/api/settings", body={"scheduler_windows": []})
        assert st == 200 and res["warnings"] == []
        _wait(lambda: _status(srv, link.id) == "finished")
        assert "wait_reason" not in srv.manager.get(link.id).extra


def test_bad_schedule_window_is_reported_not_fatal():
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/settings",
                      body={"scheduler_enabled": True, "scheduler_windows": [{"start": "25:00", "end": "02:00"}]})
        assert st == 200 and res["warnings"] and "schedule window 1" in res["warnings"][0]


def test_speed_profiles_schedule_limit_and_effective_rate():
    with running_server({"speed_limit_kbps": 2000}) as srv:
        st, flow = req(srv, "GET", "/api/flow")
        assert flow["active_profile"] == "Normal" and flow["effective_limit_bps"] == 2000 * 1024
        st, res = req(srv, "POST", "/api/speed/profile", body={"name": "gaming"})
        assert st == 200 and res["active_profile"] == "Gaming" and res["effective_limit_bps"] == 512 * 1024
        assert srv.manager._global_limiter.rate == 512 * 1024
        assert srv.manager.settings.speed_profiles["active"] == "Gaming"  # persisted
        assert req(srv, "POST", "/api/speed/profile", body={"name": "nope"})[0] == 404
        req(srv, "POST", "/api/speed/profile", body={"name": "Normal"})
        req(srv, "POST", "/api/settings", body={"scheduler_enabled": True,
                                                "scheduler_windows": [_window("limit", 100)]})
        assert req(srv, "GET", "/api/flow")[1]["effective_limit_bps"] == 100 * 1024


def test_data_cap_stops_new_downloads(site):
    urls = [site.add(f"/c/{i}.bin", BLOB) for i in range(2)]
    cap = {"enabled": True, "limit_mb": 0.25, "period": "day", "action": "stop"}
    with running_server({"data_cap": cap, "max_parallel": 1}) as srv:
        a = srv.manager.add_url(urls[0])
        _wait(lambda: _status(srv, a.id) == "finished")
        _wait(lambda: req(srv, "GET", "/api/flow")[1]["data_cap"]["exceeded"])
        b = srv.manager.add_url(urls[1])
        l = _wait(lambda: srv.manager.get(b.id).extra.get("wait_reason") and srv.manager.get(b.id))
        assert l.extra["wait_reason"] == "Data cap reached"


def test_named_queue_pause_move_and_delete(site):
    url = site.add("/q/night.bin", BLOB)
    with running_server() as srv:
        st, q = req(srv, "POST", "/api/queues", body={"name": "Night", "max_active": 1})
        assert st == 201 and q["name"] == "Night"
        assert req(srv, "POST", "/api/queues", body={"name": "night"})[0] == 400
        assert req(srv, "POST", "/api/queues/Night", body={"paused": True})[0] == 200
        st, out = req(srv, "POST", "/api/urls", body={"urls": [url], "queue": "Night"})
        lid = out[0]["id"]
        l = _wait(lambda: srv.manager.get(lid).extra.get("wait_reason") and srv.manager.get(lid))
        assert l.extra["wait_reason"] == "Queue Night is paused" and l.extra["queue"] == "Night"
        st, out = req(srv, "POST", "/api/urls", body={"urls": [url + "?x"], "queue": "Missing"})
        assert "error" in out[0]
        assert req(srv, "POST", "/api/queues/Night", body={"paused": False})[0] == 200
        _wait(lambda: _status(srv, lid) == "finished")
        # queue config persists in settings; deleting moves links back to Main
        assert srv.manager.settings.queues["queues"][1]["name"] == "Night"
        assert req(srv, "POST", f"/api/links/{lid}/move", body={"queue": "Night"})[1]["queue"] == "Night"
        st, res = req(srv, "DELETE", "/api/queues/Night")
        assert st == 200 and res["moved_to_main"] == 1 and "queue" not in srv.manager.get(lid).extra
        assert req(srv, "DELETE", "/api/queues/Main")[0] == 400


def test_retry_uses_the_shared_backoff_policy():
    with running_server({"retry_max": 3, "retry_policy": {"base_delay": 1, "factor": 3, "max_delay": 5}}) as srv:
        r = srv.manager.flow.retry
        assert [r.delay_for(i) for i in (1, 2, 3)] == [1.0, 3.0, 5.0]
        assert r.max_attempts == 4


def test_resume_restarts_when_the_server_file_changed(tmp_path):
    from opengrab.engine.downloader import LinkDownload
    from opengrab.models import Link
    from opengrab.settings import Settings
    s = FileSite()
    try:
        data_v1 = os.urandom(5 * 1024 * 1024)
        url = s.add("/big.bin", data_v1)
        s.etags["/big.bin"] = '"v1"'
        settings = Settings(download_dir=str(tmp_path), per_link_chunks=4)
        link = Link(url=url, filename="big.bin")
        dl = LinkDownload(link, url, tmp_path, settings=settings)
        s.delay_per_64k = 0.02
        import threading
        t = threading.Thread(target=lambda: _swallow(dl.run), daemon=True)
        t.start()
        _wait(lambda: os.path.exists(str(tmp_path / "big.bin.part.json")) and dl.progress()[1] > 300000)
        dl.stop_event.set()
        t.join(10)
        import json
        meta = json.loads((tmp_path / "big.bin.part.json").read_text())
        assert meta["validators"]["etag"] == '"v1"'
        # the server now has a different file under the same URL
        data_v2 = os.urandom(5 * 1024 * 1024)
        s.files["/big.bin"] = data_v2
        s.etags["/big.bin"] = '"v2"'
        s.delay_per_64k = 0
        link2 = Link(url=url, filename="big.bin")
        path = LinkDownload(link2, url, tmp_path, settings=settings).run()
        assert hashlib.sha256(Path(path).read_bytes()).digest() == hashlib.sha256(data_v2).digest()
        assert link2.extra["note"] == "restarted: the file changed on the server"
    finally:
        s.close()


def _swallow(fn):
    try:
        fn()
    except Exception:
        pass
