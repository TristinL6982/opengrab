r"""List the user-facing English strings of the web UI (M14b).

Collects text nodes and the placeholder/title/aria-label attributes of
index.html, plus string literals that app.js puts on screen (toast(...),
textContent = ..., confirm(...), labels in template literals that are plain
words). Prints JSON; --missing LANG lists strings without a translation.

    .venv\Scripts\python.exe tools\i18n_extract.py --missing zh-CN
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "opengrab" / "ui" / "static"
ATTRS = ("placeholder", "title", "aria-label")


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found: set[str] = set()
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "svg"):
            self._skip += 1
        for k, v in attrs:
            if k in ATTRS and v and _wordy(v):
                self.found.add(_norm(v))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and _wordy(data):
            self.found.add(_norm(data))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _wordy(s: str) -> bool:
    s = _norm(s)
    return bool(s) and bool(re.search(r"[A-Za-z]{2,}", s)) and not s.startswith(("http", "{", "C:\\", "<"))


JS_PATTERNS = [
    r'toast\("([^"$`]+)"',
    r'textContent = "([^"]+)"',
    r'confirm\("([^"]+)"\)',
    r'"([A-Z][a-z][^"<>${}`]{1,60})"',  # capitalised UI phrases in plain string literals
]


def extract() -> list[str]:
    c = _Collector()
    c.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    found = set(c.found)
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for pat in JS_PATTERNS:
        for m in re.finditer(pat, js):
            s = _norm(m.group(1))
            if _wordy(s) and not re.search(r"[/\\#.]\w|^[A-Z_]+$|\(\)|=>", s):
                found.add(s)
    return sorted(found)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--missing", metavar="LANG")
    args = ap.parse_args(argv)
    strings = extract()
    if args.missing:
        table = json.loads((STATIC / "i18n" / f"{args.missing}.json").read_text(encoding="utf-8"))
        missing = [s for s in strings if s not in table.get("strings", {})]
        print(json.dumps(missing, ensure_ascii=False, indent=1))
        return 1 if missing else 0
    print(json.dumps(strings, ensure_ascii=False, indent=1))
    print(len(strings), "strings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
