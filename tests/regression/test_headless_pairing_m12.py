"""M12j: headless/Docker pairing code printed at startup."""
from pathlib import Path

from opengrab import app, cli
from opengrab.remote import SCOPE_MASTER

ROOT = Path(__file__).resolve().parents[2]


class _Access:
    def __init__(self):
        self.scopes = []

    def pair_start(self, scope):
        self.scopes.append(scope)
        return 200, {"code": "ABCD-EFGH", "expires_in": 300}


class _Server:
    def __init__(self):
        self.access = _Access()


def test_pair_on_start_flag_and_env():
    assert app.pair_on_start_requested(True, env={}) is True
    assert app.pair_on_start_requested(False, env={}) is False
    assert app.pair_on_start_requested(False, env={"OPENGRAB_PAIR_ON_START": "1"}) is True
    assert app.pair_on_start_requested(False, env={"OPENGRAB_PAIR_ON_START": "yes"}) is True
    assert app.pair_on_start_requested(False, env={"OPENGRAB_PAIR_ON_START": "0"}) is False


def test_banner_uses_master_scope_and_shows_code():
    srv = _Server()
    line = app.headless_pairing_banner(srv)
    assert srv.access.scopes == [SCOPE_MASTER]
    assert "ABCD-EFGH" in line and "5 min" in line


def test_cli_passes_pair_only_to_headless(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path))
    calls = {}

    def fake_headless(settings, host=None, port=None, urls=(), pair=False):
        calls["headless"] = pair

    def fake_gui(settings, host=None, port=None, urls=()):
        calls["gui"] = True

    monkeypatch.setattr(app, "run_headless_mode", fake_headless)
    monkeypatch.setattr(app, "run_gui_mode", fake_gui)
    monkeypatch.setattr(cli, "setup_logging", lambda: None, raising=False)
    assert cli.main(["--headless", "--pair", "--port", "5999"]) == 0
    assert calls == {"headless": True}


def test_compose_enables_pairing_on_start():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "OPENGRAB_PAIR_ON_START" in text