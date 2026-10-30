"""M14b: every UI string has a Simplified Chinese translation (or is a
technical name kept as is), and the patterns compile and apply."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "opengrab" / "ui" / "static"
sys.path.insert(0, str(ROOT / "tools"))
import i18n_extract  # noqa: E402


def _table(lang):
    return json.loads((STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def test_every_ui_string_is_translated_to_chinese():
    t = _table("zh-CN")
    known = set(t["strings"]) | set(t["keep"])
    missing = [s for s in i18n_extract.extract() if s not in known]
    assert missing == [], f"untranslated strings: {missing[:20]}"


def test_translations_are_real():
    t = _table("zh-CN")
    loanwords = {"Tracker", "Webhook", "Windows Defender"}  # kept in English in Chinese software
    for en, zh in t["strings"].items():
        assert zh.strip(), en
        if len(en) > 3 and re.search(r"[a-z]{3}", en):
            assert re.search(r"[一-鿿]", zh) or zh in loanwords, (en, zh)


def test_patterns_translate_dynamic_text():
    pats = [(re.compile(a), b) for a, b in _table("zh-CN")["patterns"]]

    def tr(s):
        for re_, rep in pats:
            if re_.search(s):
                return re_.sub(rep.replace("$", "\\"), s)
        return s

    assert tr("Downloading 42%") == "正在下载 42%"
    assert tr("Waiting for a free slot on example.com") == "等待 example.com 的空闲连接"
    assert tr("3 active") == "3 个进行中"


def test_runtime_and_picker_are_wired():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="i18n.js"') < html.index('src="app.js"')
    assert 'id="set-ui_language"' in html and 'value="zh-CN"' in html
    assert _table("en")["strings"] == {}


def test_every_form_field_has_an_accessible_label():
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_label = 0
            self.for_ids = set()
            self.fields = []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "label":
                self.in_label += 1
                if a.get("for"):
                    self.for_ids.add(a["for"])
            if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
                self.fields.append((a.get("id"), bool(self.in_label or a.get("aria-label") or a.get("title"))))

        def handle_endtag(self, tag):
            if tag == "label" and self.in_label:
                self.in_label -= 1

    p = P()
    p.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    unlabeled = [fid for fid, ok in p.fields if not ok and fid not in p.for_ids]
    assert unlabeled == []


def test_toolbar_buttons_keep_spoken_names_and_toasts_are_announced():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.count('class="tb"') == 10
    assert len(re.findall(r'class="tb" id="[^"]+" title="[^"]+" aria-label="', html)) == 10
    assert 'id="toasts" role="status" aria-live="polite"' in html
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'role="progressbar"' in js and 'aria-selected=' in js and 'ev.key === "ArrowDown"' in js


def test_first_run_setup_dialog_exists():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="welcome-dialog"' in html and 'id="wel-done"' in html
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "onboarding_done: true" in js
