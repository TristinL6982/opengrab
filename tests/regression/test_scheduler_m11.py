from datetime import datetime, time

import pytest

from opengrab.scheduler import (Window, active_window, downloads_allowed, parse_hhmm,
                                speed_limit_kbps, window_from_dict)

MON = 26  # 2026-10-26 is a Monday


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m)


def test_parse_hhmm_ok():
    assert parse_hhmm("07:30") == time(7, 30)
    assert parse_hhmm(" 00:00 ") == time(0, 0)


@pytest.mark.parametrize("bad", ["24:00", "7", "ab:cd", "12:60", "1:2:3"])
def test_parse_hhmm_bad(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)


def test_day_window_bounds():
    w = Window(time(9), time(17))
    assert w.contains(at(MON, 9)) is True
    assert w.contains(at(MON, 16, 59)) is True
    assert w.contains(at(MON, 17)) is False
    assert w.contains(at(MON, 8, 59)) is False


def test_weekday_filter():
    w = Window(time(9), time(17), frozenset({5, 6}))
    assert w.contains(at(MON, 10)) is False
    assert w.contains(at(31, 10)) is True  # Saturday


def test_overnight_window_belongs_to_start_day():
    w = Window(time(23), time(6), frozenset({0}))
    assert w.contains(at(MON, 23, 30)) is True
    assert w.contains(at(27, 2)) is True  # Tuesday early morning
    assert w.contains(at(MON, 2)) is False
    assert w.contains(at(27, 23, 30)) is False


def test_window_from_dict():
    w = window_from_dict({"start": "01:00", "end": "05:00", "action": "limit", "speed_kbps": 500, "days": [0, 1]})
    assert w == Window(time(1), time(5), frozenset({0, 1}), "limit", 500)
    assert window_from_dict({"start": "01:00", "end": "05:00"}).days == frozenset(range(7))


@pytest.mark.parametrize("d", [
    {"start": "01:00", "end": "05:00", "action": "stop"},
    {"start": "01:00", "end": "05:00", "days": [7]},
    {"start": "01:00", "end": "05:00", "days": []},
    {"start": "01:00", "end": "05:00", "speed_kbps": -1},
])
def test_window_from_dict_bad(d):
    with pytest.raises(ValueError):
        window_from_dict(d)


def test_last_matching_window_wins():
    a = Window(time(0), time(23, 59), action="allow")
    b = Window(time(9), time(17), action="pause")
    assert active_window([a, b], at(MON, 10)) is b
    assert active_window([b, a], at(MON, 10)) is a
    assert active_window([b], at(MON, 20)) is None


def test_downloads_allowed_and_speed():
    pause = Window(time(9), time(17), action="pause")
    limit = Window(time(18), time(22), action="limit", speed_kbps=300)
    ws = [pause, limit]
    assert downloads_allowed(ws, at(MON, 10)) is False
    assert downloads_allowed(ws, at(MON, 19)) is True
    assert downloads_allowed([], at(MON, 10)) is True
    assert speed_limit_kbps(ws, at(MON, 19)) == 300
    assert speed_limit_kbps(ws, at(MON, 10)) == 0
    assert speed_limit_kbps(ws, at(MON, 23)) == 0