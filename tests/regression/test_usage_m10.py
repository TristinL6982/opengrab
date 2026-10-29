"""Account usage rules (M10a)."""
import pytest

from opengrab.usage import DAY, UsageRule, UsageTracker, day_key

T0 = DAY * 20000 + 10 * 3600  # 10:00 UTC


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_rule_rejects_bad_values():
    with pytest.raises(ValueError):
        UsageRule(daily_bytes=-1)
    with pytest.raises(ValueError):
        UsageRule(max_parallel=-1)
    with pytest.raises(ValueError):
        UsageRule(reset_hour_utc=24)


def test_day_key_respects_reset_hour():
    assert day_key(T0) == 20000
    assert day_key(T0, 12) == 19999
    assert day_key(T0 + 3 * 3600, 12) == 20000


def test_record_accumulates_and_remaining():
    clock = Clock(T0)
    tr = UsageTracker(clock)
    rule = UsageRule(daily_bytes=1000)
    assert tr.record("A1", 300, rule) == 300
    assert tr.record("A1", 200, rule) == 500
    assert tr.used("A1", rule) == 500
    assert tr.remaining("A1", rule) == 500
    assert tr.used("A2", rule) == 0


def test_quota_exhausted_blocks_start():
    tr = UsageTracker(Clock(T0))
    rule = UsageRule(daily_bytes=1000)
    tr.record("A1", 1500, rule)
    assert tr.remaining("A1", rule) == 0
    assert tr.can_start("A1", rule) is False


def test_unlimited_rule():
    tr = UsageTracker(Clock(T0))
    rule = UsageRule()
    tr.record("A1", 10 ** 12, rule)
    assert tr.remaining("A1", rule) is None
    assert tr.can_start("A1", rule) is True


def test_quota_resets_at_reset_hour():
    clock = Clock(T0)
    tr = UsageTracker(clock)
    rule = UsageRule(daily_bytes=1000, reset_hour_utc=12)
    tr.record("A1", 1000, rule)
    clock.t = T0 + 2 * 3600 - 60  # 11:59 UTC
    assert tr.used("A1", rule) == 1000
    assert tr.can_start("A1", rule) is False
    clock.t = T0 + 2 * 3600 + 60  # 12:01 UTC
    assert tr.used("A1", rule) == 0
    assert tr.can_start("A1", rule) is True


def test_max_parallel_limit():
    tr = UsageTracker(Clock(T0))
    rule = UsageRule(max_parallel=2)
    assert tr.can_start("A1", rule, active=1) is True
    assert tr.can_start("A1", rule, active=2) is False


def test_negative_bytes_rejected():
    tr = UsageTracker(Clock(T0))
    with pytest.raises(ValueError):
        tr.record("A1", -5)


def test_round_trip_dict():
    clock = Clock(T0)
    tr = UsageTracker(clock)
    rule = UsageRule(daily_bytes=1000)
    tr.record("A1", 400, rule)
    data = tr.to_dict()
    assert data == {"A1": [20000, 400]}
    tr2 = UsageTracker.from_dict(data, clock)
    assert tr2.used("A1", rule) == 400
    assert tr2.remaining("A1", rule) == 600