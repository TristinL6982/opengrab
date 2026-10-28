"""M6g: DownloadManager persists its queue in session.db (SessionDB) and migrates links.json."""
import json
import shutil
import tempfile
from pathlib import Path

from opengrab.engine import manager as mgr_mod
from opengrab.engine.manager import DownloadManager
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings


def _iso(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="og-m6g-"))
    monkeypatch.setattr(mgr_mod, "app_data_dir", lambda: d)
    return d


def _mgr(d):
    return DownloadManager(Settings(download_dir=str(d / "dl")))


def _close(*ms):
    for m in ms:
        if m._db is not None:
            m._db.close()
            m._db = None


def test_save_and_load(monkeypatch):
    d = _iso(monkeypatch)
    m1 = m2 = None
    try:
        m1 = _mgr(d)
        links = [Link(url=f"http://example.com/f{i}.zip") for i in range(3)]
        with m1._lock:
            for l in links:
                m1._links[l.id] = l
        m1._save_now()
        assert (d / "session.db").exists()
        assert not (d / "links.json").exists()
        m2 = _mgr(d)
        m2._load_links()
        got = list(m2._links.values())
        assert [l.id for l in got] == [l.id for l in links]
        assert [l.url for l in got] == [l.url for l in links]
    finally:
        _close(*(m for m in (m1, m2) if m))
        shutil.rmtree(d, ignore_errors=True)


def test_downloading_comes_back_paused(monkeypatch):
    d = _iso(monkeypatch)
    m1 = m2 = None
    try:
        m1 = _mgr(d)
        l = Link(url="http://example.com/a.bin", status=LinkStatus.DOWNLOADING)
        with m1._lock:
            m1._links[l.id] = l
        m1._save_now()
        m2 = _mgr(d)
        m2._load_links()
        got = m2._links[l.id]
        assert got.status == LinkStatus.PAUSED
        assert got.extra.get("note")
    finally:
        _close(*(m for m in (m1, m2) if m))
        shutil.rmtree(d, ignore_errors=True)


def test_migration(monkeypatch):
    d = _iso(monkeypatch)
    m1 = m2 = None
    try:
        a = Link(url="http://example.com/a.zip")
        b = Link(url="http://example.com/b.zip")
        (d / "links.json").write_text(json.dumps(
            [json.loads(a.model_dump_json()), json.loads(b.model_dump_json())]), encoding="utf-8")
        m1 = _mgr(d)
        m1._load_links()
        assert list(m1._links) == [a.id, b.id]
        assert (d / "links.json.migrated").exists()
        assert not (d / "links.json").exists()
        m2 = _mgr(d)
        m2._load_links()
        assert list(m2._links) == [a.id, b.id]
    finally:
        _close(*(m for m in (m1, m2) if m))
        shutil.rmtree(d, ignore_errors=True)


def test_corrupt_legacy_entry_skipped(monkeypatch):
    d = _iso(monkeypatch)
    m = None
    try:
        a = Link(url="http://example.com/a.zip")
        (d / "links.json").write_text(json.dumps(
            [{"id": "bad", "status": "nonsense-status"}, json.loads(a.model_dump_json())]), encoding="utf-8")
        m = _mgr(d)
        m._load_links()
        assert list(m._links) == [a.id]
    finally:
        _close(*(x for x in (m,) if x))
        shutil.rmtree(d, ignore_errors=True)


def test_shutdown_closes_db(monkeypatch):
    d = _iso(monkeypatch)
    m = None
    try:
        m = _mgr(d)
        m._session()
        assert m._db is not None
        m.shutdown()
        assert m._db is None
        assert (d / "session.db").exists()
    finally:
        _close(*(x for x in (m,) if x))
        shutil.rmtree(d, ignore_errors=True)
