import os

import pytest

from opengrab.categories import (DEFAULT_CATEGORIES, OTHER, Category, CategoryRules, ext_of,
                                 normalize_ext)


def test_normalize_ext():
    assert normalize_ext("MP4") == ".mp4"
    assert normalize_ext(" .Mp4 ") == ".mp4"
    with pytest.raises(ValueError):
        normalize_ext("  ")
    with pytest.raises(ValueError):
        normalize_ext(".")


def test_ext_of():
    assert ext_of("Movie.MKV") == ".mkv"
    assert ext_of("backup.tar.gz") == ".tar.gz"
    assert ext_of("C:/dl/setup.exe") == ".exe"
    assert ext_of("file.zip?token=abc") == ".zip"
    assert ext_of("README") == ""
    assert ext_of(".bashrc") == ""
    assert ext_of("") == ""


def test_default_categories():
    rules = CategoryRules()
    assert rules.names() == list(DEFAULT_CATEGORIES)
    assert rules.category_for("clip.mp4") == "Video"
    assert rules.category_for("song.FLAC") == "Music"
    assert rules.category_for("paper.pdf") == "Documents"
    assert rules.category_for("src.tar.gz") == "Archives"
    assert rules.category_for("setup.msi") == "Programs"
    assert rules.category_for("data.xyz") == OTHER
    assert rules.category_for("noext") == OTHER


def test_double_suffix_falls_back_to_last_part():
    rules = CategoryRules([Category("Gz", ("gz",))])
    assert rules.category_for("a.tar.gz") == "Gz"


def test_category_validation_and_dedupe():
    c = Category("Books", ("EPUB", ".epub", "mobi"))
    assert c.extensions == (".epub", ".mobi")
    assert c.folder == "Books"
    with pytest.raises(ValueError):
        Category(" ", (".x",))


def test_set_replace_remove_and_order():
    rules = CategoryRules()
    rules.set_category(Category("Books", ("epub",)))
    assert rules.names()[-1] == "Books"
    assert rules.category_for("novel.epub") == "Documents"
    rules.set_category(Category("documents", ("pdf",)))
    assert rules.names().index("documents") == 2
    assert rules.category_for("novel.epub") == "Books"
    assert rules.remove("BOOKS") is True
    assert rules.remove("missing") is False
    assert rules.get("music").name == "Music"
    assert rules.get("nope") is None


def test_folder_for():
    base = os.path.join("D:" + os.sep, "Downloads")
    rules = CategoryRules([Category("Video", ("mp4",), "Videos"), Category("Abs", ("iso",), os.path.abspath("isos"))])
    assert rules.folder_for("a.mp4", base) == os.path.join(base, "Videos")
    assert rules.folder_for("a.iso", base) == os.path.abspath("isos")
    assert rules.folder_for("a.bin", base) == base


def test_roundtrip():
    rules = CategoryRules([Category("Video", ("mp4", "mkv"), "Vids")])
    data = rules.to_list()
    assert data == [{"name": "Video", "extensions": [".mp4", ".mkv"], "folder": "Vids"}]
    again = CategoryRules.from_list(data)
    assert again.to_list() == data
    assert CategoryRules.from_list(None).names() == list(DEFAULT_CATEGORIES)
    assert CategoryRules.from_list([]).names() == list(DEFAULT_CATEGORIES)