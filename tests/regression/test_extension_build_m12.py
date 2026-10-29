# M12i: Firefox build of the extension (tools/build_extension.py).
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "build_extension.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_extension", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _src_manifest():
    return json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))


def test_builds_both_zips(tmp_path):
    res = _load().build(tmp_path)
    ver = _src_manifest()["version"]
    assert res["version"] == ver
    assert Path(res["chromium"]).name == f"opengrab-chromium-{ver}.zip"
    assert Path(res["firefox"]).name == f"opengrab-firefox-{ver}.zip"
    for key in ("chromium", "firefox"):
        with zipfile.ZipFile(res[key]) as z:
            names = set(z.namelist())
        for f in ("manifest.json", "background.js", "rules.js", "content.js", "options.html", "options.js"):
            assert f in names, (key, f)
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


def test_chromium_zip_matches_source(tmp_path):
    res = _load().build(tmp_path)
    with zipfile.ZipFile(res["chromium"]) as z:
        m = json.loads(z.read("manifest.json"))
        bg = z.read("background.js").decode("utf-8")
    assert m == _src_manifest()
    assert m["background"] == {"service_worker": "background.js"}
    assert 'importScripts("rules.js")' in bg


def test_firefox_manifest_and_background(tmp_path):
    res = _load().build(tmp_path)
    with zipfile.ZipFile(res["firefox"]) as z:
        m = json.loads(z.read("manifest.json"))
        bg = z.read("background.js").decode("utf-8")
    assert m["manifest_version"] == 3
    assert m["background"] == {"scripts": ["rules.js", "background.js"]}
    assert "service_worker" not in json.dumps(m["background"])
    gecko = m["browser_specific_settings"]["gecko"]
    assert gecko["id"] == "opengrab@opengrab.app"
    assert gecko["strict_min_version"]
    assert "minimum_chrome_version" not in m
    assert m["options_ui"]["page"] == "options.html"
    assert "options_page" not in m
    assert m["permissions"] == _src_manifest()["permissions"]
    assert 'importScripts("rules.js")' not in bg
    assert "chrome.downloads.onCreated.addListener" in bg


def test_cli_writes_to_out_dir(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(TOOL), "--out", str(out)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert Path(data["chromium"]).is_file()
    assert Path(data["firefox"]).is_file()
    assert len(list(out.glob("*.zip"))) == 2