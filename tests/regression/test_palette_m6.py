import json
import os
import shutil
import subprocess

ROOT = "C:/Users/trist/opengrab"
STATIC = os.path.join(ROOT, "src", "opengrab", "ui", "static")
PALETTE = os.path.join(STATIC, "palette.js")
NODE = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"


def _read(name):
    with open(os.path.join(STATIC, name), "r", encoding="utf-8") as f:
        return f.read()


def test_node_check():
    r = subprocess.run([NODE, "--check", PALETTE], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_fuzzy_functions():
    script = (
        "const p = require(" + json.dumps(PALETTE) + ");\n"
        "console.log(JSON.stringify({\n"
        "  prefixScore: p.ogFuzzyScore('pa all', 'Pause all'),\n"
        "  noMatch: p.ogFuzzyScore('xyz', 'Pause all'),\n"
        "  empty: p.ogFuzzyScore('', 'anything'),\n"
        "  settings: p.ogFuzzyScore('set', 'Settings'),\n"
        "  resetAll: p.ogFuzzyScore('set', 'Reset all'),\n"
        "  pause: (p.ogPaletteRank('pause', [{title:'Go to Queue'},{title:'Pause all'},{title:'Resume all'}])),\n"
        "  speed: p.ogPaletteRank('speed', [{title:'Setting: Limit', keywords:'speed bandwidth'}]).length,\n"
        "  order: (p.ogPaletteRank('', [{title:'A'},{title:'B'},{title:'C'}])),\n"
        "  limit: p.ogPaletteRank('', Array.from({length:25}, (_, i) => ({title:'Item ' + i}))).length\n"
        "}));\n"
    )
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip())
    assert out["prefixScore"] > 0
    assert out["noMatch"] == -1
    assert out["empty"] == 1
    assert out["settings"] > out["resetAll"]
    assert out["pause"] == ["Pause all"]
    assert out["speed"] == 1
    assert out["order"] == ["A", "B", "C"]
    assert out["limit"] == 20


def test_index_html():
    content = _read("index.html")
    assert 'id="palette"' in content
    assert 'id="palette-input"' in content
    assert 'id="palette-list"' in content
    app_idx = content.find('<script src="app.js"></script>')
    palette_idx = content.find('<script src="palette.js"></script>')
    assert app_idx != -1
    assert palette_idx != -1
    assert palette_idx > app_idx


def test_style_css():
    content = _read("style.css")
    assert "#palette" in content
    assert "#palette-list li" in content
    assert "li.sel" in content


def test_palette_js_contents():
    content = _read("palette.js")
    for needle in ("Pause all", "Resume all", "Clear finished", "metaKey", "ctrlKey", "Escape"):
        assert needle in content, needle
    assert "innerHTML" not in content