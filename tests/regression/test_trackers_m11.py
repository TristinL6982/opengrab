from opengrab.trackers import (
    DEFAULT_TRACKERS,
    TrackerManager,
    normalize_tracker,
    parse_tracker_list,
)


def test_normalize_tracker():
    assert normalize_tracker(" UDP://Tracker.Example.COM:1337/announce ") == "udp://tracker.example.com:1337/announce"
    assert normalize_tracker("https://t.example.org/announce?k=1#frag") == "https://t.example.org/announce?k=1"
    assert normalize_tracker("ftp://t.example.org/announce") is None
    assert normalize_tracker("not a url") is None
    assert normalize_tracker("udp://host:99999/announce") is None
    assert normalize_tracker("") is None


def test_parse_tracker_list_skips_comments_invalid_and_duplicates():
    text = "# public list\nudp://a.example:80/announce\n\nhttp://b.example/announce\nbogus\nUDP://A.example:80/announce\n"
    assert parse_tracker_list(text) == ["udp://a.example:80/announce", "http://b.example/announce"]
    assert parse_tracker_list("") == []


def test_defaults_add_remove():
    mgr = TrackerManager()
    assert mgr.urls() == DEFAULT_TRACKERS
    empty = TrackerManager(trackers=[])
    assert empty.urls() == []
    assert empty.add("udp://a.example:80/announce") is True
    assert empty.add("UDP://a.example:80/announce") is False
    assert empty.add("garbage") is False
    assert empty.remove("udp://a.example:80/announce") is True
    assert empty.remove("udp://a.example:80/announce") is False
    assert empty.urls() == []


def test_merge_and_needs_update():
    mgr = TrackerManager(trackers=["udp://a.example:80/announce"], update_interval=100)
    assert mgr.needs_update(5.0) is True
    added = mgr.merge("udp://a.example:80/announce\nhttp://b.example/announce\n", now=1000.0)
    assert added == 1
    assert mgr.urls() == ["udp://a.example:80/announce", "http://b.example/announce"]
    assert mgr.needs_update(1050.0) is False
    assert mgr.needs_update(1100.0) is True


def test_record_announce_and_health():
    mgr = TrackerManager(trackers=["udp://a.example:80/announce"], max_failures=2)
    url = "udp://a.example:80/announce"
    mgr.record_announce(url, ok=True, seeds=10, peers=4, now=1.0)
    h = mgr.health(url)
    assert (h.successes, h.seeds, h.peers, h.last_announce) == (1, 10, 4, 1.0)
    mgr.record_announce(url, ok=False, error="timeout", now=2.0)
    assert mgr.is_healthy(url) is True
    mgr.record_announce(url, ok=False, error="timeout", now=3.0)
    assert mgr.is_healthy(url) is False
    assert mgr.healthy_urls() == []
    assert mgr.health(url).last_error == "timeout"
    mgr.record_announce(url, ok=True, seeds=1, peers=1, now=4.0)
    assert mgr.is_healthy(url) is True
    assert mgr.health(url).failures == 2
    assert mgr.health(url).last_error == ""


def test_record_announce_unknown_tracker_is_ignored():
    mgr = TrackerManager(trackers=[])
    mgr.record_announce("udp://x.example:1/announce", ok=True)
    assert mgr.health("udp://x.example:1/announce") is None
    assert mgr.is_healthy("udp://x.example:1/announce") is False


def test_for_torrent_merges_and_removes():
    mgr = TrackerManager(trackers=["udp://g1.example:1/announce", "udp://g2.example:2/announce"], max_failures=1)
    mgr.record_announce("udp://g2.example:2/announce", ok=False)
    result = mgr.for_torrent(
        own=["http://own.example/announce", "udp://g1.example:1/announce"],
        extra=["udp://extra.example:3/announce", "bad"],
        removed=["http://own.example/announce"],
    )
    assert result == ["udp://g1.example:1/announce", "udp://extra.example:3/announce"]


def test_to_dict_from_dict_round_trip():
    mgr = TrackerManager(trackers=["udp://a.example:80/announce"], max_failures=5, update_interval=60)
    mgr.merge("http://b.example/announce", now=500.0)
    mgr.record_announce("http://b.example/announce", ok=True, seeds=3, peers=2, now=501.0)
    data = mgr.to_dict()
    copy = TrackerManager.from_dict(data)
    assert copy.urls() == mgr.urls()
    assert copy.to_dict() == data
    assert copy.health("http://b.example/announce").seeds == 3