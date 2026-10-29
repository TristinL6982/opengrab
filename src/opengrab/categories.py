"""Download categories (M11g): map file names to categories and per-category folders by file-type rules."""
from __future__ import annotations

import os
from dataclasses import dataclass

DOUBLE_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")

DEFAULT_CATEGORIES = {
    "Video": (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts"),
    "Music": (".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".opus", ".wma"),
    "Documents": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".epub", ".odt"),
    "Archives": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"),
    "Programs": (".exe", ".msi", ".msix", ".apk", ".dmg", ".appimage", ".deb", ".rpm"),
}

OTHER = "Other"


def normalize_ext(ext: str) -> str:
    """'MP4', '.Mp4' or ' mp4 ' -> '.mp4'. Empty input raises ValueError."""
    e = (ext or "").strip().lower()
    if not e or e == ".":
        raise ValueError("empty extension")
    return e if e.startswith(".") else "." + e


def ext_of(filename: str) -> str:
    """Lowercase extension of a file name, keeping known double suffixes like .tar.gz; '' when none."""
    base = os.path.basename((filename or "").strip().split("?")[0]).lower()
    for d in DOUBLE_SUFFIXES:
        if base.endswith(d) and len(base) > len(d):
            return d
    root, ext = os.path.splitext(base)
    return ext if root else ""


@dataclass
class Category:
    name: str
    extensions: tuple[str, ...]
    folder: str = ""

    def __post_init__(self) -> None:
        if not (self.name or "").strip():
            raise ValueError("empty category name")
        self.extensions = tuple(dict.fromkeys(normalize_ext(e) for e in self.extensions))
        if not self.folder:
            self.folder = self.name


class CategoryRules:
    def __init__(self, categories: list[Category] | None = None) -> None:
        if categories is None:
            categories = [Category(n, exts) for n, exts in DEFAULT_CATEGORIES.items()]
        self._cats: list[Category] = []
        for c in categories:
            self.set_category(c)

    def names(self) -> list[str]:
        return [c.name for c in self._cats]

    def get(self, name: str) -> Category | None:
        for c in self._cats:
            if c.name.lower() == (name or "").lower():
                return c
        return None

    def set_category(self, cat: Category) -> None:
        """Add or replace a category by name (case-insensitive), keeping its position when replacing."""
        for i, c in enumerate(self._cats):
            if c.name.lower() == cat.name.lower():
                self._cats[i] = cat
                return
        self._cats.append(cat)

    def remove(self, name: str) -> bool:
        before = len(self._cats)
        self._cats = [c for c in self._cats if c.name.lower() != (name or "").lower()]
        return len(self._cats) != before

    def category_for(self, filename: str) -> str:
        """First category whose rules match; a double suffix (.tar.gz) is tried before its last part (.gz)."""
        ext = ext_of(filename)
        if not ext:
            return OTHER
        candidates = [ext]
        if ext in DOUBLE_SUFFIXES:
            candidates.append("." + ext.rsplit(".", 1)[1])
        for cand in candidates:
            for c in self._cats:
                if cand in c.extensions:
                    return c.name
        return OTHER

    def folder_for(self, filename: str, base_dir: str) -> str:
        """Download folder: base_dir joined with the category folder; base_dir itself for Other."""
        name = self.category_for(filename)
        cat = self.get(name)
        if cat is None:
            return base_dir
        if os.path.isabs(cat.folder):
            return cat.folder
        return os.path.join(base_dir, cat.folder)

    def to_list(self) -> list[dict]:
        return [{"name": c.name, "extensions": list(c.extensions), "folder": c.folder} for c in self._cats]

    @classmethod
    def from_list(cls, data: list[dict] | None) -> "CategoryRules":
        if not data:
            return cls()
        return cls([Category(str(d["name"]), tuple(d.get("extensions") or ()), str(d.get("folder") or ""))
                    for d in data])