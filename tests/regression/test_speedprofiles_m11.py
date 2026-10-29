import calendar

import pytest

from opengrab.speedprofiles import (
    DAY, MONTH, SLOW, STOP, UNLIMITED,
    DataCap, DataCapTracker, ProfileSet, SpeedProfile, default_profiles, period_key,
)


def ts(y, m, d, h=12):
    return float(calendar.timegm((y, m, d, h, 0, 0)))


def test_profile_validation_and_roundtrip():
    p = SpeedProfile(" Gaming ", download_limit=1000, upload_limit=50)
    assert p.name == "Gaming"
    assert SpeedProfile.from_dict(p.to_dict()) == p
    with pytest.raises(ValueError):
        SpeedProfile("")
    with pytest.raises(ValueError):
        SpeedProfile("x", download_limit=-1)


def test_default_profiles():
    names = [p.name for p in default_profiles()]
    assert names == ["Normal", "Gaming", "Night"]
    assert default_profiles()[0].download_limit == UNLIMITED


def test_profileset_switch_case_insensitive():
    ps = ProfileSet()
    assert ps.active.name == "Normal"
    assert ps.switch("gaming").name == "Gaming"
    assert ps.active.download_limit == 512 * 1024
    with pytest.raises(KeyError):
        ps.switch("nope")


def test_profileset_remove_and_last_profile_kept():
    ps = ProfileSet([SpeedProfile("A"), SpeedProfile("B")], active="B")
    assert ps.remove("b") is True
    assert ps.active.name == "A"
    assert ps.remove("A") is False
    assert ps.remove("missing") is False
    assert ps.names() == ["A"]


def test_profileset_roundtrip_and_bad_active():
    ps = ProfileSet()
    ps.add(SpeedProfile("Work", download_limit=2000))
    ps.switch("Work")
    restored = ProfileSet.from_dict(ps.to_dict())
    assert restored.active.name == "Work"
    assert restored.names() == ["Normal", "Gaming", "Night", "Work"]
    assert ProfileSet([SpeedProfile("A")], active="zzz").active.name == "A"


def test_period_key():
    assert period_key(ts(2026, 3, 5), DAY) == "2026-03-05"
    assert period_key(ts(2026, 3, 5), MONTH) == "2026-03"
    with pytest.raises(ValueError):
        period_key(0.0, "week")


def test_datacap_validation():
    with pytest.raises(ValueError):
        DataCap(0)
    with pytest.raises(ValueError):
        DataCap(10, period="week")
    with pytest.raises(ValueError):
        DataCap(10, action="pause")


def test_tracker_stop_cap_and_daily_reset():
    t = DataCapTracker(DataCap(1000, period=DAY, action=STOP))
    t.add(600, ts(2026, 3, 5, 1))
    assert t.remaining(ts(2026, 3, 5, 2)) == 400
    assert t.allows_download(ts(2026, 3, 5, 2)) is True
    t.add(400, ts(2026, 3, 5, 3))
    assert t.exceeded(ts(2026, 3, 5, 3)) is True
    assert t.allows_download(ts(2026, 3, 5, 3)) is False
    assert t.used(ts(2026, 3, 6, 0)) == 0
    assert t.allows_download(ts(2026, 3, 6, 0)) is True
    assert t.history == {"2026-03-05": 1000}
    with pytest.raises(ValueError):
        t.add(-1, ts(2026, 3, 6))


def test_tracker_slow_cap_monthly():
    t = DataCapTracker(DataCap(500, period=MONTH, action=SLOW, slow_limit=100))
    assert t.effective_limit(UNLIMITED, ts(2026, 3, 1)) == UNLIMITED
    t.add(500, ts(2026, 3, 2))
    assert t.allows_download(ts(2026, 3, 20)) is True
    assert t.effective_limit(UNLIMITED, ts(2026, 3, 20)) == 100
    assert t.effective_limit(50, ts(2026, 3, 20)) == 50
    assert t.effective_limit(1000, ts(2026, 3, 20)) == 100
    assert t.effective_limit(1000, ts(2026, 4, 1)) == 1000


def test_disabled_cap_never_exceeded():
    t = DataCapTracker(DataCap(10, enabled=False))
    t.add(100, ts(2026, 3, 5))
    assert t.exceeded(ts(2026, 3, 5)) is False
    assert t.allows_download(ts(2026, 3, 5)) is True