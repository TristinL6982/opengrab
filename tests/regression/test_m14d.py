"""M14d: installer, release pipeline and the installer's add-on option."""
import re
from pathlib import Path

from opengrab import cli

ROOT = Path(__file__).resolve().parents[2]


def test_install_addon_flag_installs_and_can_enable_ocr(monkeypatch, tmp_path):
    from opengrab import addons
    from opengrab.settings import load_settings
    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(addons, "install", lambda name, **kw: calls.append(name) or {"ok": True})
    assert cli.main(["--install-addon", "ocr"]) == 0
    assert calls == ["ocr"] and load_settings().captcha_ocr_enabled is False
    assert cli.main(["--install-addon", "ocr", "--enable-ocr"]) == 0
    assert load_settings().captcha_ocr_enabled is True

    def boom(name, **kw):
        raise RuntimeError("offline")
    monkeypatch.setattr(addons, "install", boom)
    assert cli.main(["--install-addon", "torrent"]) == 1


def test_installer_offers_everything_the_spec_lists():
    iss = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8")
    assert r"DefaultDirName={autopf}\OpenGrab" in iss            # C:\Program Files\OpenGrab
    assert r'DestDir: "{app}\app"' in iss and r'Name: "{app}\plugins"' in iss and r'Name: "{app}\models"' in iss
    for task in ("desktopicon", "autostart", "magnet", "opengrabproto", "torrent", "crawljob", "dlc", "ocr", r"ocr\on"):
        assert re.search(rf'^Name: "{re.escape(task)}"', iss, re.M), task
    # the add-on is fetched by OpenGrab as the signed-in user, with its own progress bar
    assert "ExecAsOriginalUser" in iss and "'--install-addon ocr --progress-file" in iss
    assert "' --enable-ocr'" in iss and r"WizardIsTaskSelected('ocr\on')" in iss
    assert "OcrBar" in iss and "ProgressGauge" in iss and "PumpMessages" in iss
    assert "runhidden" not in iss  # no silent step that makes the wizard look frozen
    assert r"{userappdata}\OpenGrab" in iss                      # data kept unless the user asks
    assert "uninsdeletekey" in iss and "uninsdeletevalue" in iss
    # the registry entries match what Settings, Windows integration writes
    from opengrab import winintegration as wi
    assert wi.PROGID in iss and wi.RUN_KEY in iss and "--minimized" in iss
    assert (ROOT / "build" / "opengrab.ico").is_file()


def test_release_assets_match_what_the_updater_looks_for():
    from opengrab import updater
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert updater.asset_name("v1.2.3") == "OpenGrab-1.2.3-win64.zip"
    assert "OpenGrab-$VERSION-win64.zip.sig" in rel and "sign_release.py" in rel
    assert "secrets.RELEASE_SIGNING_KEY" in rel
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for needle in ("ubuntu-latest", "macos-latest", "docker build", "Health.Status", "installer.iss",
                   "/VERYSILENT"):
        assert needle in ci, needle


def test_no_private_key_is_committed():
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if not tracked:  # not a git checkout (e.g. an sdist)
        return
    marker = "PRIVATE" + " KEY-----"  # split so this file does not match itself
    for name in tracked:
        p = ROOT / name
        if p.suffix.lower() in (".png", ".ico", ".zip", ".exe", ".whl") or not p.is_file():
            continue
        assert marker not in p.read_text(encoding="utf-8", errors="ignore"), name


def test_install_addon_progress_file_for_the_setup_wizard(monkeypatch, tmp_path):
    from opengrab import addons
    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path))
    prog = tmp_path / "progress.txt"
    seen = []

    def fake_install(name, progress=None, **kw):
        for done, step in ((0, "downloading a.whl"), (512 * 1024, "downloading a.whl"),
                           (1024 * 1024, "unpacking a.whl")):
            progress({"done": done, "total": 1024 * 1024, "step": step})
            seen.append(prog.read_text())
        return {"ok": True}

    monkeypatch.setattr(addons, "install", fake_install)
    assert cli.main(["--install-addon", "ocr", "--progress-file", str(prog)]) == 0
    assert seen[0] == "0|0|1048576|downloading a.whl"
    assert seen[-1] == "100|1048576|1048576|unpacking a.whl"  # a new step is always written
    assert prog.read_text() == "done"

    def failing(name, progress=None, **kw):
        progress({"done": 10, "total": 100, "step": "downloading a.whl"})
        raise RuntimeError("network down |" + chr(10) + "retry later")

    monkeypatch.setattr(addons, "install", failing)
    assert cli.main(["--install-addon", "ocr", "--progress-file", str(prog)]) == 1
    assert prog.read_text() == "error|network down / retry later"
