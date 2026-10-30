"""M14a: appearance settings and the remote-access settings that were missing."""
import os
from pathlib import Path

from tests.helpers.apiserver import req, running_server

STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"


def test_remote_access_settings_reach_the_policy(tmp_path, monkeypatch):
    with running_server({"allow_remote_http": True, "trusted_proxies": ["172.18.0.1"]}) as srv:
        pol = srv.access.policy if hasattr(srv.access, "policy") else None
        assert pol is not None and pol.allow_remote_http is True and pol.trusted_proxies == ["172.18.0.1"]


def test_env_overrides_for_docker(tmp_path, monkeypatch):
    from opengrab.settings import load_settings
    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENGRAB_ALLOW_REMOTE_HTTP", "1")
    monkeypatch.setenv("OPENGRAB_TRUSTED_PROXIES", "10.0.0.1, 10.0.0.2")
    s = load_settings()
    assert s.allow_remote_http is True and s.trusted_proxies == ["10.0.0.1", "10.0.0.2"]
    monkeypatch.setenv("OPENGRAB_ALLOW_REMOTE_HTTP", "0")
    assert load_settings().allow_remote_http is False


def test_appearance_settings_round_trip():
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/settings", body={
            "ui_theme": "classic-dark", "ui_accent": "#c42b1c", "ui_density": "compact",
            "ui_hidden_columns": ["eta", "speed"], "ui_toolbar_labels": False})
        assert st == 200 and set(res["applied"]) >= {"ui_theme", "ui_density", "ui_hidden_columns"}
        s = req(srv, "GET", "/api/settings")[1]
        assert s["ui_theme"] == "classic-dark" and s["ui_hidden_columns"] == ["eta", "speed"]
        assert s["ui_language"] == "auto" and s["onboarding_done"] is False


def test_theme_css_defines_dark_tokens_and_column_switches():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"]' in css and "--window:" in css
    for col in ("size", "status", "eta", "speed", "progress"):
        assert f".hide-col-{col}" in css
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function applyUi(" in js and "prefers-color-scheme: dark" in js
