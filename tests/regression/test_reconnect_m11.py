import pytest

from opengrab.reconnect import ReconnectConfig, ReconnectResult, Reconnector, config_from_dict


def _noop():
    return None


def test_config_defaults_and_parsing():
    assert config_from_dict({}) == ReconnectConfig()
    assert config_from_dict(None) == ReconnectConfig(False, 300, 600, 3)
    cfg = config_from_dict({"enabled": True, "min_wait_seconds": "60", "cooldown_seconds": 0, "max_attempts": 2})
    assert cfg == ReconnectConfig(True, 60, 0, 2)


@pytest.mark.parametrize("bad", [{"max_attempts": 0}, {"min_wait_seconds": -1}, {"cooldown_seconds": "x"}])
def test_config_rejects_bad(bad):
    with pytest.raises(ValueError):
        config_from_dict(bad)


def test_should_reconnect_rules():
    clock = lambda: 0.0
    off = Reconnector(ReconnectConfig(enabled=False), _noop, lambda: "1.1.1.1", clock)
    assert off.should_reconnect(10_000) is False
    on = Reconnector(ReconnectConfig(enabled=True, min_wait_seconds=300), _noop, lambda: "1.1.1.1", clock)
    assert on.should_reconnect(299) is False
    assert on.should_reconnect(300) is True
    assert on.should_reconnect(300, active_downloads=1) is False


def test_success_first_try():
    ips = iter(["1.1.1.1", "2.2.2.2"])
    calls = []
    r = Reconnector(ReconnectConfig(enabled=True), lambda: calls.append(1), lambda: next(ips), lambda: 0.0)
    result = r.reconnect()
    assert result == ReconnectResult(True, "1.1.1.1", "2.2.2.2", 1, "")
    assert len(calls) == 1
    assert r.history == [result]


def test_retries_until_ip_changes():
    ips = iter(["1.1.1.1", "1.1.1.1", "3.3.3.3"])
    calls = []
    r = Reconnector(ReconnectConfig(enabled=True, max_attempts=3), lambda: calls.append(1), lambda: next(ips), lambda: 0.0)
    assert r.reconnect() == ReconnectResult(True, "1.1.1.1", "3.3.3.3", 2, "")
    assert len(calls) == 2


def test_gives_up_when_ip_never_changes():
    r = Reconnector(ReconnectConfig(enabled=True, max_attempts=2), _noop, lambda: "1.1.1.1", lambda: 0.0)
    assert r.reconnect() == ReconnectResult(False, "1.1.1.1", "1.1.1.1", 2, "ip unchanged")


def test_reconnect_action_errors_count_as_attempts():
    def boom():
        raise RuntimeError("router down")

    r = Reconnector(ReconnectConfig(enabled=True, max_attempts=2), boom, lambda: "1.1.1.1", lambda: 0.0)
    assert r.reconnect() == ReconnectResult(False, "1.1.1.1", "1.1.1.1", 2, "reconnect failed: router down")


def test_ip_lookup_errors_mean_unknown():
    def no_ip():
        raise OSError("offline")

    r = Reconnector(ReconnectConfig(enabled=True, max_attempts=1), _noop, no_ip, lambda: 0.0)
    assert r.reconnect() == ReconnectResult(False, "", "", 1, "ip unchanged")


def test_cooldown_after_reconnect():
    now = [1000.0]
    r = Reconnector(ReconnectConfig(enabled=True, min_wait_seconds=0, cooldown_seconds=600), _noop,
                    lambda: "1.1.1.1", lambda: now[0])
    assert r.next_allowed_at() is None
    assert r.should_reconnect(10) is True
    r.reconnect()
    assert r.next_allowed_at() == 1600.0
    assert r.should_reconnect(10) is False
    now[0] = 1599.0
    assert r.should_reconnect(10) is False
    now[0] = 1600.0
    assert r.should_reconnect(10) is True