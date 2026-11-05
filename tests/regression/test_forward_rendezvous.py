"""A launch carrying only a target reaches the copy that is actually running."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from opengrab import app, single_instance as si, winintegration as wi


class _Fake:
    """A fake OpenGrab API that records what it was sent and checks the token."""

    def __init__(self, token):
        self.got = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ok(self, code=200):
                if self.headers.get("Authorization") != f"Bearer {token}":
                    code = 401
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
                return code

            def do_GET(self):
                self._ok()

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self._ok() == 200:
                    outer.got.append((self.path, body))

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def rv(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENGRAB_RENDEZVOUS_DIR", str(tmp_path / "rv"))
    monkeypatch.delenv("OPENGRAB_DATA_DIR", raising=False)
    made = []

    def instance(name, token, exe, alive=True):
        d = tmp_path / name
        d.mkdir()
        (d / "settings.json").write_text(json.dumps({"api_token": token}))
        fake = _Fake(token)
        made.append(fake)
        entry = {"pid": os.getpid(), "exe": si.norm_path(exe), "data_dir": si.norm_path(d),
                 "host": "127.0.0.1", "port": fake.port, "base_path": ""}
        return d, fake, entry

    def publish(*entries):
        si._write_entries(list(entries))

    yield instance, publish
    for f in made:
        f.close()


def test_forward_picks_instance_with_matching_exe(rv, monkeypatch):
    instance, publish = rv
    da, fa, ea = instance("a", "tok-a", "C:/installed/OpenGrab.exe")
    db, fb, eb = instance("b", "tok-b", sys.executable)
    publish(ea, eb)
    found = si.find_instance(exe=sys.executable)
    assert found["port"] == fb.port
    assert si.forward_to_entry(found, ["magnet:?xt=urn:btih:abc"], ["x.torrent"])
    assert ("/api/open", {"targets": ["x.torrent"]}) in fb.got
    assert ("/api/urls", {"urls": ["magnet:?xt=urn:btih:abc"]}) in fa.got + fb.got
    assert fa.got == []


def test_explicit_data_dir_targets_its_own_instance(rv):
    instance, publish = rv
    da, fa, ea = instance("a", "tok-a", sys.executable)
    db, fb, eb = instance("b", "tok-b", sys.executable)
    publish(ea, eb)
    assert si.find_instance(data_dir=da)["port"] == fa.port
    assert si.find_instance(data_dir=db)["port"] == fb.port


def test_stale_entries_are_ignored(rv):
    instance, publish = rv
    d, f, e = instance("a", "tok", sys.executable)
    dead_pid = dict(e, pid=2_000_000_000)
    publish(dead_pid)
    assert si.find_instance(exe=sys.executable) is None
    f.close()  # port now closed
    publish(e)
    assert si.find_instance(exe=sys.executable) is None
    assert si.find_instance(data_dir=d) is None


def test_register_drops_dead_entries_and_unregister_removes_own(rv, tmp_path):
    instance, publish = rv
    d, f, e = instance("a", "tok", sys.executable)
    publish(dict(e, pid=2_000_000_000, data_dir="gone"))
    si.register_instance("127.0.0.1", f.port, "", d)
    entries = si._read_entries()
    assert [x["data_dir"] for x in entries] == [si.norm_path(d)]
    assert "tok" not in json.dumps(entries)  # the token is never written here
    si.unregister_instance()
    assert si._read_entries() == []


def _no_gui_deps(monkeypatch):
    for mod in ("webview", "pystray"):
        monkeypatch.setitem(sys.modules, mod, type(sys)(mod))
    pil = type(sys)("PIL")
    pil.Image = pil.ImageDraw = object()
    monkeypatch.setitem(sys.modules, "PIL", pil)


def test_failed_forward_shows_the_error(rv, monkeypatch, tmp_path):
    instance, publish = rv
    d, f, e = instance("a", "tok", sys.executable)
    publish(e)
    _no_gui_deps(monkeypatch)
    shown = []
    monkeypatch.setattr(si, "message_box", lambda t, m: shown.append((t, m)))
    monkeypatch.setattr(app, "report_forward_failure", si.report_forward_failure)
    monkeypatch.setattr(app, "forward_to_entry", lambda *a, **k: False)
    app.run_gui_mode(None, urls=["magnet:?xt=urn:btih:abc"])
    assert len(shown) == 1 and "could not hand" in shown[0][1]


def test_launch_with_only_target_forwards_without_starting(rv, monkeypatch):
    instance, publish = rv
    d, f, e = instance("b", "tok-b", sys.executable)
    publish(e)
    _no_gui_deps(monkeypatch)
    monkeypatch.setattr(app, "acquire", lambda name: pytest.fail("must not start a second copy"))
    app.run_gui_mode(None, files=["C:/x/a.torrent"])
    assert ("/api/open", {"targets": ["C:/x/a.torrent"]}) in f.got


def test_held_lock_with_dead_port_reports(rv, monkeypatch, tmp_path):
    _, publish = rv
    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path / "held"))
    _no_gui_deps(monkeypatch)
    shown = []
    monkeypatch.setattr(si, "message_box", lambda t, m: shown.append(m))
    monkeypatch.setattr(app, "acquire", lambda name: None)
    app.run_gui_mode(None, urls=["http://h.example/a.bin"])
    assert len(shown) == 1 and "could not hand" in shown[0]


def test_launch_command_carries_startup_options(monkeypatch):
    monkeypatch.setattr(wi, "STARTUP_OPTIONS",
                        {"data_dir": r"C:\t\data", "port": 5790, "download_dir": r"C:\t\dl"})
    cmd = wi.launch_command()
    assert r'--data-dir "C:\t\data"' in cmd and "--port 5790" in cmd and r'--download-dir "C:\t\dl"' in cmd
    monkeypatch.setattr(wi, "STARTUP_OPTIONS", {"data_dir": None, "port": None, "download_dir": None})
    assert "--data-dir" not in wi.launch_command() and "--port" not in wi.launch_command()


def test_cli_records_startup_options(monkeypatch, tmp_path):
    from opengrab import cli
    monkeypatch.setattr(wi, "STARTUP_OPTIONS", {"data_dir": None, "port": None, "download_dir": None})
    monkeypatch.setenv("OPENGRAB_DATA_DIR", "")
    monkeypatch.delenv("OPENGRAB_DOWNLOAD_DIR", raising=False)
    monkeypatch.setattr(app, "run_gui_mode", lambda *a, **k: None)
    monkeypatch.setattr(cli, "setup_logging", lambda: None, raising=False)
    assert cli.main(["--data-dir", str(tmp_path), "--port", "5790", "--download-dir", str(tmp_path / "dl")]) == 0
    assert wi.STARTUP_OPTIONS["port"] == 5790
    assert wi.STARTUP_OPTIONS["data_dir"] == str(tmp_path)
    assert wi.STARTUP_OPTIONS["download_dir"] == str(tmp_path / "dl")
