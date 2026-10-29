import pytest

from opengrab.notifications import KINDS, Notification, NotificationCenter, download_notification


def make(**kw):
    ticks = iter(range(100, 1000))
    return NotificationCenter(clock=lambda: next(ticks), **kw)


def test_download_notification():
    assert KINDS == ("finished", "failed", "info")
    assert download_notification("a.zip", "Completed") == ("finished", "Download finished", "a.zip")
    assert download_notification("a.zip", "failed", " 404 ") == ("failed", "Download failed", "a.zip: 404")
    assert download_notification("", "error") == ("failed", "Download failed", "download")
    with pytest.raises(ValueError):
        download_notification("a.zip", "paused")


def test_add_and_history_newest_first():
    c = make()
    a = c.add("info", "Hello", "x")
    b = c.notify_download("b.iso", "done")
    assert a == Notification(1, "info", "Hello", "x", 100.0)
    assert b.id == 2
    assert [n.id for n in c.items()] == [2, 1]
    with pytest.raises(ValueError):
        c.add("warning", "t")


def test_max_items_trims_oldest():
    c = make(max_items=2)
    for i in range(3):
        c.add("info", f"n{i}")
    assert [n.title for n in c.items()] == ["n2", "n1"]
    with pytest.raises(ValueError):
        NotificationCenter(max_items=0)


def test_read_state():
    c = make()
    c.add("info", "a")
    c.add("info", "b")
    assert c.unread_count() == 2
    assert c.mark_read(1) is True
    assert c.mark_read(99) is False
    assert c.unread_count() == 1
    c.mark_all_read()
    assert c.unread_count() == 0
    c.clear()
    assert c.items() == []


def test_toaster_called_disabled_and_errors_swallowed():
    calls = []
    c = make(toaster=lambda t, m: calls.append((t, m)))
    c.notify_download("a.zip", "failed", "timeout")
    assert calls == [("Download failed", "a.zip: timeout")]
    off = make(toaster=lambda t, m: calls.append((t, m)), toasts_enabled=False)
    off.add("info", "quiet")
    assert len(calls) == 1

    def boom(t, m):
        raise RuntimeError("no toast")

    bad = make(toaster=boom)
    assert bad.add("info", "still stored").id == 1
    assert len(bad.items()) == 1


def test_persistence_roundtrip():
    c = make()
    c.add("info", "a", "m")
    c.add("failed", "b")
    c.mark_read(1)
    data = c.to_list()
    assert data[0] == {"id": 1, "kind": "info", "title": "a", "message": "m", "time": 100.0, "read": True}
    d = make()
    d.load_list(data)
    assert [n.id for n in d.items()] == [2, 1]
    assert d.unread_count() == 1
    assert d.add("info", "c").id == 3