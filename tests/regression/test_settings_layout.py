"""Settings layout toggle: Indexed (default, options-dialog style) | List."""
import json
import re
import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"


def _read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_toggle_and_index_markup():
    html = _read("index.html")
    assert 'id="settings-layout"' in html and 'aria-label="Settings layout"' in html
    assert 'data-layout="indexed" aria-pressed="true"' in html
    assert 'data-layout="list" aria-pressed="false"' in html
    assert 'id="settings-index"' in html and 'id="settings-index-list"' in html
    assert 'id="settings-index-select"' in html and 'aria-label="Settings section"' in html
    # toggle lives in the search bar; index comes before the first section
    bar, idx = html.index('id="settings-layout"'), html.index('id="settings-index"')
    assert html.index('id="settings-search"') < bar < idx < html.index("<h2>Appearance</h2>")


def test_js_hooks_default_indexed_and_shared_grouping():
    js = _read("app.js")
    for needle in ("OG_SETTINGS_INDEX", "og_settings_layout", "og_settings_section",
                   "function settingsSections(", "function settingsKids(",
                   '=== "list" ? "list" : "indexed"', "ArrowDown", "ArrowUp", "aria-current",
                   "window.OG_SETTINGS_INDEX?.reveal(el)", "window.OG_SETTINGS_INDEX?.refresh()"):
        assert needle in js, needle
    # the search reuses the same grouping helper instead of its own child filter
    assert "const kids = settingsKids(pane);" in js
    # every storage access is guarded
    block = js[js.index("settings layout: Indexed"):]
    assert "try { return localStorage.getItem" in block and "try { localStorage.setItem" in block


def test_css_layouts_and_narrow_dropdown():
    css = _read("style.css")
    for needle in (".ix-hide", "#settings-index", ".six-item.sel", ".six-item.dim", ".six-count",
                   "max-width: 699px", "#settings-index-select", '.seg button[aria-pressed="true"]'):
        assert needle in css, needle
    assert ".savebar" in css


def test_layout_strings_translated():
    t = json.loads((STATIC / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))["strings"]
    for s in ("Indexed", "List", "Settings layout", "Settings sections", "Settings section"):
        assert re.search(r"[一-鿿]", t.get(s, "")), s
    assert (STATIC / "i18n" / "zh-CN.json").read_bytes().endswith(b"\n")


def test_section_names_unique_for_remembered_selection():
    html = _read("index.html")
    pane = html[html.index('id="tab-settings"'):]
    heads = re.findall(r"\n        <h2>(.*?)</h2>", pane)   # top-level headings only
    assert len(heads) > 20 and len(heads) == len(set(heads))
    assert "savebar" in pane
