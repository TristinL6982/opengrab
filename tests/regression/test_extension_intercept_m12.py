"""M12f part 3: extension download interception (rules.js parity, background wiring)."""
import json
import os
import shutil
import subprocess

import pytest

from opengrab.extrules import should_intercept

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXT = os.path.join(ROOT, "extension")


def _read(name):
    with open(os.path.join(EXT, name), "r", encoding="utf-8") as fh:
        return fh.read()


RULES = {
    "enabled": True,
    "file_types": ["zip", "iso", "mp4"],
    "min_size_bytes": 1000,
    "sites": {"example.com": False, "dl.example.com": True, "blocked.org": False},
    "bypass_key": "alt",
}

CASES = [
    {"url": "https://files.test/a.zip"},
    {"url": "https://files.test/a.ZIP?x=1"},
    {"url": "https://files.test/a.txt"},
    {"url": "https://files.test/get", "filename": "movie.mp4"},
    {"url": "https://files.test/get", "filename": "C:\\Users\\me\\Downloads\\movie.mp4"},
    {"url": "https://files.test/a.zip", "size": 10},
    {"url": "https://files.test/a.zip", "size": 5000},
    {"url": "https://files.test/a.zip", "size": None},
    {"url": "https://www.example.com/a.zip"},
    {"url": "https://dl.example.com/a.zip"},
    {"url": "https://files.test/a.zip", "page_url": "https://blocked.org/page"},
    {"url": "https://files.test/a.zip", "bypass": True},
    {"url": "https://files.test/noext"},
]


def _python_verdicts(rules):
    out = []
    for c in CASES:
        fn = c.get("filename")
        if fn:
            fn = fn.replace("\\", "/")
        out.append(should_intercept(rules, c["url"], filename=fn, size=c.get("size"),
                                    page_url=c.get("page_url"),
                                    bypass_pressed=c.get("bypass", False)))
    return out


def _node_verdicts(rules):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        "const R = require(%s);\n"
        "const rules = %s; const cases = %s;\n"
        "console.log(JSON.stringify(cases.map(c => R.shouldIntercept(rules, {url: c.url,"
        " filename: c.filename, size: c.size, pageUrl: c.page_url, bypassPressed: !!c.bypass}))));\n"
    ) % (json.dumps(os.path.join(EXT, "rules.js")), json.dumps(rules), json.dumps(CASES))
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_rules_js_matches_python_rules():
    assert _node_verdicts(RULES) == _python_verdicts(RULES)


def test_rules_js_matches_python_defaults_and_disabled():
    assert _node_verdicts({}) == _python_verdicts({})
    off = dict(RULES, enabled=False)
    assert _node_verdicts(off) == [False] * len(CASES)
    nobypass = dict(RULES, bypass_key="none")
    assert _node_verdicts(nobypass) == _python_verdicts(nobypass)


def test_extension_scripts_parse():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    for name in ("rules.js", "content.js", "background.js"):
        res = subprocess.run([node, "--check", os.path.join(EXT, name)],
                             capture_output=True, text=True, timeout=60)
        assert res.returncode == 0, name + ": " + res.stderr


def test_manifest_has_download_permissions_and_content_script():
    m = json.loads(_read("manifest.json"))
    for perm in ("downloads", "cookies", "contextMenus", "storage"):
        assert perm in m["permissions"]
    scripts = [js for cs in m["content_scripts"] for js in cs["js"]]
    assert "content.js" in scripts
    for js in scripts:
        assert os.path.isfile(os.path.join(EXT, js))


def test_background_intercepts_and_hands_off():
    bg = _read("background.js")
    assert bg.lstrip().splitlines()[0].startswith("// ")
    assert 'importScripts("rules.js")' in bg
    assert "chrome.downloads.onCreated.addListener" in bg
    assert "chrome.downloads.cancel" in bg
    assert "/api/extension/config" in bg
    assert "/api/extension/add" in bg
    assert "chrome.cookies.getAll" in bg
    assert "OpenGrabRules.shouldIntercept" in bg
    assert "opengrab-bypass" in bg and "opengrab-bypass" in _read("content.js")
    # the original context-menu handoff must keep working
    assert "/api/urls" in bg and "contextMenus.create" in bg
