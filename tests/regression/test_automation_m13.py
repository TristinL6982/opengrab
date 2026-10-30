"""M13: event hooks, webhooks, Folder Watch, anti-standby, when-done, CLI client."""
import hashlib
import hmac
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from opengrab import automation as auto
from opengrab import client_cli
from tests.helpers.apiserver import MASTER, req, running_server
from tests.helpers.filesite import FileSite


def _wait(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


def test_hook_gets_event_details_in_its_environment(tmp_path, site):
    out = tmp_path / "hook.txt"
    script = tmp_path / "hook.py"
    script.write_text("import os, sys\nopen(sys.argv[1], 'w').write("
                      "os.environ['OPENGRAB_EVENT'] + '|' + os.environ.get('OPENGRAB_FILENAME', ''))\n")
    url = site.add("/h/report.pdf", b"%PDF-1.4 test")
    cmd = f'"{sys.executable}" "{script}" "{out}"'
    with running_server({"event_hooks": [{"event": "finished", "command": cmd}]}) as srv:
        srv.manager.add_url(url)
        _wait(lambda: out.exists() and out.read_text())
        assert out.read_text() == "finished|report.pdf"
        hist = req(srv, "GET", "/api/automation")[1]["history"]
        assert any(h.get("kind") == "hook" and h["code"] == 0 for h in hist)


def test_bad_hooks_and_webhooks_are_reported():
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/settings", body={
            "event_hooks": [{"event": "nope", "command": "x"}], "webhooks": [{"url": "ftp://x"}]})
        assert any("unknown event" in w for w in res["warnings"]) and any("http" in w for w in res["warnings"])


class _Receiver(BaseHTTPRequestHandler):
    got: list = []
    fail_first = 0

    def log_message(self, *_a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).got.append((dict(self.headers), body))
        code = 500 if type(self).fail_first > 0 else 204
        type(self).fail_first -= 1
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_webhook_is_signed_and_retried(site):
    _Receiver.got, _Receiver.fail_first = [], 1
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    hook_url = f"http://127.0.0.1:{httpd.server_address[1]}/hook"
    url = site.add("/w/file.bin", b"x" * 1000)
    try:
        with running_server({"webhooks": [{"url": hook_url, "events": ["finished"], "secret": "s3cret"}]}) as srv:
            srv.manager.add_url(url)
            _wait(lambda: len(_Receiver.got) >= 2, timeout=30)
            headers, body = _Receiver.got[-1]
            payload = json.loads(body)
            assert payload["event"] == "finished" and payload["download"]["filename"] == "file.bin"
            want = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
            sig = {k.lower(): v for k, v in headers.items()}["x-opengrab-signature"]
            assert hmac.compare_digest(sig, want)
            _wait(lambda: any(h.get("kind") == "webhook" and h.get("code") == 204
                              for h in req(srv, "GET", "/api/automation")[1]["history"]))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_crawljob_parsing():
    jobs = auto.parse_crawljob(
        "text=https://a.test/1.zip https://a.test/2.zip\npackageName=Holiday\nautoStart=TRUE\n"
        "extractPasswords=[\"pw1\",\"pw2\"]\n\ntext=https://b.test/x.rar\n")
    assert [j.urls for j in jobs] == [["https://a.test/1.zip", "https://a.test/2.zip"], ["https://b.test/x.rar"]]
    assert jobs[0].package == "Holiday" and jobs[0].auto_start is True and jobs[0].passwords == ["pw1", "pw2"]
    assert jobs[1].auto_start is None
    js = auto.parse_crawljob(json.dumps([{"text": "https://c.test/y.iso", "packageName": "P", "autoStart": "FALSE"}]))
    assert js[0].urls == ["https://c.test/y.iso"] and js[0].auto_start is False


def test_folder_watch_imports_and_archives(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "links.txt").write_text("see https://host.test/a.zip and https://host.test/b.zip\n")
    (watch / "fav.url").write_text("[InternetShortcut]\nURL=https://host.test/c.iso\n")
    (watch / "job.crawljob").write_text("text=https://host.test/d.7z\npackageName=Batch\nautoStart=FALSE\n")
    old = time.time() - 30
    for p in watch.iterdir():
        os.utime(p, (old, old))
    with running_server({"folder_watch": [{"path": str(watch), "mode": "review"}]}) as srv:
        svc = srv.features["automation"]
        svc.tick()
        cands = {c["url"]: c for c in req(srv, "GET", "/api/linkgrabber")[1]}
        assert set(cands) == {"https://host.test/a.zip", "https://host.test/b.zip",
                              "https://host.test/c.iso", "https://host.test/d.7z"}
        assert cands["https://host.test/d.7z"]["package"] == "Batch"
        assert sorted(p.name for p in (watch / "added").iterdir()) == ["fav.url", "job.crawljob", "links.txt"]
        assert not any(p.is_file() for p in watch.iterdir())


def test_stay_awake_sets_and_clears_the_flag():
    calls = []
    s = auto.StayAwake(setter=lambda f: calls.append(f) or 1)
    s.hold(True)
    _wait(lambda: s.active)
    s.hold(False)
    _wait(lambda: not s.active)
    assert calls == [auto.ES_CONTINUOUS | auto.ES_SYSTEM_REQUIRED, auto.ES_CONTINUOUS]


def test_anti_standby_follows_downloads(site):
    site.delay_per_64k = 0.2
    url = site.add("/s/slow.bin", os.urandom(400 * 1024))
    with running_server() as srv:
        svc = srv.features["automation"]
        calls = []
        svc.awake = auto.StayAwake(setter=lambda f: calls.append(f) or 1)
        link = srv.manager.add_url(url)
        _wait(lambda: srv.manager.get(link.id).status.value == "downloading")
        svc.tick()
        _wait(lambda: svc.awake.active)
        _wait(lambda: srv.manager.get(link.id).status.value == "finished", 30)
        svc.tick()
        _wait(lambda: not svc.awake.active)


def test_when_done_counts_down_runs_once_and_can_be_cancelled(site):
    url = site.add("/d/f.bin", b"y" * 500)
    with running_server({"when_done": "sleep", "when_done_delay_s": 5}) as srv:
        svc = srv.features["automation"]
        ran = []
        svc.executor = lambda argv: ran.append(argv)
        srv.manager.add_url(url)
        _wait(lambda: svc.countdown_action == "sleep", 30)
        st, info = req(srv, "GET", "/api/automation")
        assert info["when_done"]["pending"] == "sleep" and info["when_done"]["seconds_left"] <= 5
        _wait(lambda: ran, 15)
        assert ran[0] == auto.when_done_command("sleep")
        assert srv.manager.settings.when_done == "nothing"  # one-time by default
        assert any(n["title"] == "Downloads done" for n in req(srv, "GET", "/api/notifications")[1])
        # a second batch with shutdown chosen, cancelled in time
        req(srv, "POST", "/api/automation/when-done", body={"action": "shutdown"})
        srv.manager.add_url(site.add("/d/g.bin", b"z" * 500))
        _wait(lambda: svc.countdown_action == "shutdown", 30)
        assert req(srv, "POST", "/api/automation/cancel", body={})[1] == {"cancelled": True}
        time.sleep(6)
        assert len(ran) == 1
        assert req(srv, "POST", "/api/automation/when-done", body={"action": "explode"})[0] == 400


def test_when_done_commands():
    assert auto.when_done_command("nothing") is None and auto.when_done_command("exit") is None
    if sys.platform == "win32":
        assert auto.when_done_command("shutdown") == ["shutdown", "/s", "/t", "0"]


def _cli(srv, *args):
    out = io.StringIO()
    code = client_cli.run(["--url", f"http://127.0.0.1:{srv.port_used}", "--token", MASTER, *args], out=out)
    return code, out.getvalue()


def test_cli_client_against_a_running_opengrab(site):
    url = site.add("/c/one.bin", b"q" * 2000)
    with running_server() as srv:
        code, out = _cli(srv, "add", url, "--json")
        assert code == 0
        lid = json.loads(out)[0]["id"]
        _wait(lambda: srv.manager.get(lid).status.value == "finished")
        code, out = _cli(srv, "list", "--json")
        assert code == 0 and json.loads(out)[0]["status"] == "finished"
        code, out = _cli(srv, "list")
        assert lid in out and "finished" in out
        code, out = _cli(srv, "status")
        assert code == 0 and "1 finished" in out and "speed profile Normal" in out
        code, out = _cli(srv, "add", "https://x.test/r.zip", "--review")
        assert code == 0 and "LinkGrabber" in out
        code, out = _cli(srv, "remove", "all", "--json")
        assert json.loads(out)["ids"] == [lid] and srv.manager.get(lid) is None
        code, out = _cli(srv, "notifications")
        assert code == 0 and "Download finished" in out
    # not running any more: exit code 3
    out = io.StringIO()
    assert client_cli.run(["--url", "http://127.0.0.1:9", "--token", "x", "list"], out=out) == 3


def test_cli_entry_point_dispatches_client_commands(monkeypatch):
    from opengrab import cli
    seen = []
    monkeypatch.setattr(client_cli, "run", lambda argv: seen.append(argv) or 0)
    assert cli.main(["--url", "http://x", "list"]) == 0 and seen == [["--url", "http://x", "list"]]
