"""W1: notification center wired into the app, toasts, and ticketed SSE."""
import http.client
import json
import time

from tests.helpers.apiserver import req, running_server


def _finished(srv, title="movie.mkv", lid="L1"):
    srv.manager.on_change("finished", {"id": lid, "status": "finished", "title": title})


def test_finished_download_is_recorded_persisted_and_read_state(tmp_path):
    data = tmp_path / "data"
    with running_server(data_dir=data) as srv:
        _finished(srv)
        st, items = req(srv, "GET", "/api/notifications")
        assert st == 200 and items[0]["title"] == "Download finished"
        assert items[0]["message"] == "movie.mkv: finished" and items[0]["kind"] == "finished"
        assert items[0]["read"] is False and items[0]["link_id"] == "L1"
        assert req(srv, "GET", "/api/notifications/summary")[1] == {"unread": 1}
        saved = json.loads((data / "notifications.json").read_text(encoding="utf-8"))
        assert saved["items"][0]["link_id"] == "L1"
        nid = items[0]["id"]
        assert req(srv, "POST", f"/api/notifications/{nid}/read", body={})[0] == 200
        assert req(srv, "GET", "/api/notifications/summary")[1] == {"unread": 0}
        assert req(srv, "POST", "/api/notifications/999/read", body={})[0] == 404
        srv.manager.on_change("error", {"id": "L2", "status": "error", "title": "a.zip", "error": "404"})
    # restart with the same data dir: history and read state survive
    with running_server(data_dir=data) as srv:
        st, items = req(srv, "GET", "/api/notifications")
        assert [n["kind"] for n in items] == ["error", "finished"]
        assert items[0]["message"] == "a.zip: error (404)" and items[1]["read"] is True
        assert req(srv, "POST", "/api/notifications/read-all", body={})[0] == 200
        assert req(srv, "GET", "/api/notifications/summary")[1] == {"unread": 0}
        assert req(srv, "POST", "/api/notifications/clear", body={})[1] == {"cleared": 2}
        assert req(srv, "GET", "/api/notifications")[1] == []


def test_toasts_follow_the_setting():
    with running_server() as srv:
        shown = []
        srv.features["notify"].toaster = lambda t, m: shown.append((t, m))
        _finished(srv, "a.iso")
        assert shown == [("Download finished", "a.iso: finished")]
        srv.manager.settings.toasts_enabled = False
        _finished(srv, "b.iso", "L9")
        assert len(shown) == 1
        assert len(req(srv, "GET", "/api/notifications")[1]) == 2


def test_parked_state_is_announced_once_per_park():
    with running_server() as srv:
        evt = {"id": "P1", "status": "login", "title": "host file"}
        srv.manager.on_change("grabbed", dict(evt))
        srv.manager.on_change("grabbed", dict(evt))  # failed retry, same state
        srv.manager.on_change("progress", dict(evt))  # unrelated event kinds are ignored
        items = req(srv, "GET", "/api/notifications")[1]
        assert [n["kind"] for n in items] == ["login"]
        srv.manager.on_change("grabbed", {"id": "P1", "status": "waiting", "title": "host file"})
        srv.manager.on_change("grabbed", dict(evt))  # parked again later: new notice
        assert len(req(srv, "GET", "/api/notifications")[1]) == 2


def _open_sse(srv, path):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port_used, timeout=10)
    conn.request("GET", path)
    return conn, conn.getresponse()


def test_event_stream_needs_a_single_use_ticket():
    with running_server() as srv:
        conn, resp = _open_sse(srv, "/api/events")
        assert resp.status == 401
        conn.close()
        st, t = req(srv, "POST", "/api/events-ticket", body={})
        assert st == 200 and len(t["ticket"]) == 48
        assert req(srv, "POST", "/api/events-ticket", body={}, auth=False)[0] == 401
        conn, resp = _open_sse(srv, "/api/events?ticket=" + t["ticket"])
        assert resp.status == 200
        assert resp.fp.readline().startswith(b"event: hello")
        resp.fp.readline()
        _finished(srv, "x.bin", "S1")
        lines = []
        deadline = time.time() + 5
        while time.time() < deadline:
            line = resp.fp.readline()
            if line.startswith(b"data: "):
                lines.append(json.loads(line[6:]))
                if any(m["kind"] == "notification" for m in lines):
                    break
        conn.close()
        kinds = [m["kind"] for m in lines]
        assert "finished" in kinds and "notification" in kinds
        note = next(m for m in lines if m["kind"] == "notification")["payload"]
        assert note["unread"] == 1 and note["item"]["title"] == "Download finished"
        conn, resp = _open_sse(srv, "/api/events?ticket=" + t["ticket"])  # reused
        assert resp.status == 401
        conn.close()


def test_enum_status_and_untitled_link_read_cleanly():
    from opengrab.models import LinkStatus
    with running_server() as srv:
        srv.manager.on_change("grabbed", {"id": "E1", "status": LinkStatus.ERROR, "title": "",
                                          "url": "http://host.example/files/big.zip", "error": "refused"})
        item = req(srv, "GET", "/api/notifications")[1][0]
        assert item["kind"] == "error"
        assert item["message"] == "big.zip: error (refused)"
