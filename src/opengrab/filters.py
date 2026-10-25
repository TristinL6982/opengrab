"""Include/Exclude link filters (regex + glob), JD2-style."""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
import json
import threading

from .models import FilterRule


class FilterManager:
    def __init__(self):
        self.rules: list[FilterRule] = []
        self._lock = threading.Lock()
        self._compile_cache: dict[str, re.Pattern | None] = {}

    # -- persistence --------------------------------------------------------
    @property
    def _path(self):
        from .settings import app_data_dir
        return app_data_dir() / "filters.json"

    def load(self) -> None:
        p = self._path
        if p.exists():
            try:
                self.rules = [FilterRule(**r) for r in json.loads(p.read_text("utf-8"))]
            except Exception:
                self.rules = []

    def save(self) -> None:
        with self._lock:
            self._path.write_text(
                json.dumps([r.model_dump() for r in self.rules], indent=2), "utf-8")

    # -- ops -----------------------------------------------------------------
    def add(self, pattern: str, kind: str = "exclude", use_glob: bool = False,
            note: str = "") -> FilterRule:
        rule = FilterRule(pattern=pattern, kind=kind, use_glob=use_glob, note=note)
        with self._lock:
            self.rules.append(rule)
        self.save()
        return rule

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self.rules)
            self.rules = [r for r in self.rules if r.id != rule_id]
            changed = len(self.rules) != before
        if changed:
            self.save()
        return changed

    def toggle(self, rule_id: str, enabled: bool | None = None) -> None:
        with self._lock:
            for r in self.rules:
                if r.id == rule_id:
                    r.enabled = (not r.enabled) if enabled is None else enabled
        self.save()

    # -- matching -------------------------------------------------------------
    def _match(self, rule: FilterRule, target: str) -> bool:
        if rule.use_glob:
            return fnmatch.fnmatch(target, rule.pattern)
        key = rule.pattern
        pat = self._compile_cache.get(key, "MISS")
        if pat == "MISS":
            try:
                pat = re.compile(rule.pattern, re.IGNORECASE)
            except re.error:
                pat = None
            self._compile_cache[key] = pat
        if pat is None:
            return rule.pattern.lower() in target.lower()
        return pat.search(target) is not None

    def allows(self, url: str, filename: str = "") -> tuple[bool, str]:
        """Returns (allowed, reason).  Exclude rules win over include rules."""
        targets = [url] + ([filename] if filename else [])
        excludes = [r for r in self.rules if r.enabled and r.kind == "exclude"]
        for r in excludes:
            for t in targets:
                if self._match(r, t):
                    return False, f"excluded by '{r.pattern}'"
        includes = [r for r in self.rules if r.enabled and r.kind == "include"]
        if includes:
            for r in includes:
                for t in targets:
                    if self._match(r, t):
                        return True, f"matched include '{r.pattern}'"
            return False, "no include rule matched"
        return True, ""

    def snapshot(self) -> list[dict]:
        return [r.model_dump() for r in self.rules]
