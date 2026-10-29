import pytest

from opengrab.torrent import (
    HIGH,
    NORMAL,
    SKIP,
    TorrentEngine,
    TorrentSettings,
    file_priorities,
    parse_magnet,
    settings_from_dict,
    should_stop_seeding,
)

HASH = "a" * 40
URI = "magnet:?xt=urn:btih:" + HASH.upper() + "&dn=My%20File&tr=udp://t1:80&tr=udp://t1:80&tr=udp://t2:80"


class FakeHandle:
    def __init__(self, params):
        self.params = params
        self.priorities = None

    def set_priorities(self, priorities):
        self.priorities = priorities


class FakeSession:
    def __init__(self):
        self.applied = []
        self.added = []
        self.removed = []

    def apply_settings(self, data):
        self.applied.append(data)

    def add(self, params):
        handle = FakeHandle(params)
        self.added.append(handle)
        return handle

    def remove(self, handle):
        self.removed.append(handle)


def test_parse_magnet():
    m = parse_magnet(URI)
    assert m.info_hash == HASH
    assert m.name == "My File"
    assert m.trackers == ["udp://t1:80", "udp://t2:80"]


def test_parse_magnet_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_magnet("http://example.com/file.torrent")
    with pytest.raises(ValueError):
        parse_magnet("magnet:?xt=urn:btih:short")


def test_settings_from_dict_defaults_and_bad_values():
    s = settings_from_dict({"dht": False, "sequential": 1, "ratio_limit": "x", "seed_minutes": -5})
    assert s.dht is False
    assert s.pex is True
    assert s.sequential is True
    assert s.ratio_limit == 0.0
    assert s.seed_minutes == 0
    assert settings_from_dict(None) == TorrentSettings()


def test_file_priorities():
    assert file_priorities(3) == [NORMAL, NORMAL, NORMAL]
    assert file_priorities(4, selected=[0, 2, 9], high=[2]) == [NORMAL, SKIP, HIGH, SKIP]


def test_should_stop_seeding():
    s = TorrentSettings(ratio_limit=2.0, seed_minutes=10)
    assert should_stop_seeding(s, 100, 100, 0) is False
    assert should_stop_seeding(s, 200, 100, 0) is True
    assert should_stop_seeding(s, 0, 100, 600) is True
    assert should_stop_seeding(TorrentSettings(), 10**9, 1, 10**9) is False


def test_engine_add_select_and_dedupe():
    session = FakeSession()
    engine = TorrentEngine(session, TorrentSettings(sequential=True, lsd=False))
    assert session.applied == [{"enable_dht": True, "enable_lsd": False, "enable_pex": True}]
    handle = engine.add_magnet(URI, "C:/dl", extra_trackers=["udp://t2:80", "udp://t3:80"])
    assert handle.params["trackers"] == ["udp://t1:80", "udp://t2:80", "udp://t3:80"]
    assert handle.params["sequential"] is True
    assert handle.params["save_path"] == "C:/dl"
    assert engine.add_magnet(URI, "C:/other") is handle
    assert len(session.added) == 1
    assert engine.select_files(HASH, 2, selected=[1]) == [SKIP, NORMAL]
    assert handle.priorities == [SKIP, NORMAL]


def test_engine_seeding_limit_removes():
    session = FakeSession()
    engine = TorrentEngine(session, TorrentSettings(ratio_limit=1.0))
    handle = engine.add_magnet(URI, "C:/dl")
    assert engine.check_seeding(HASH, 50, 100, 0) is False
    assert engine.check_seeding(HASH, 100, 100, 0) is True
    assert session.removed == [handle]
    assert engine.remove(HASH) is False