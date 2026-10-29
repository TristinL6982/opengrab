import pytest

from opengrab.hostlimits import HostLimits, host_of

A = "https://www.Example.com/a.zip"
B = "http://example.com:8080/b.zip"
C = "https://other.org/c.zip"


def test_host_of():
    assert host_of(A) == "example.com"
    assert host_of(B) == "example.com"
    assert host_of("not a url") == ""


def test_unlimited_by_default():
    hl = HostLimits()
    for _ in range(20):
        assert hl.start(A) is True
    assert hl.active(B) == 20


def test_default_limit_shared_across_same_host():
    hl = HostLimits(default_max=2)
    assert hl.start(A) is True
    assert hl.start(B) is True
    assert hl.can_start(A) is False
    assert hl.start(A) is False
    assert hl.active(A) == 2
    assert hl.start(C) is True


def test_finish_frees_a_slot_and_never_goes_negative():
    hl = HostLimits(default_max=1)
    assert hl.start(A) is True
    assert hl.start(A) is False
    hl.finish(A)
    assert hl.active(A) == 0
    hl.finish(A)
    assert hl.active(A) == 0
    assert hl.start(A) is True


def test_per_host_override():
    hl = HostLimits(default_max=1, per_host={"WWW.example.com": 3, "other.org": 0})
    assert hl.limit_for(A) == 3
    assert hl.limit_for(C) == 0
    assert hl.limit_for("https://third.net/x") == 1
    for _ in range(3):
        assert hl.start(A) is True
    assert hl.start(A) is False
    for _ in range(5):
        assert hl.start(C) is True


def test_set_limit_live():
    hl = HostLimits(default_max=1)
    assert hl.start(A) is True
    assert hl.start(A) is False
    hl.set_limit("example.com", 2)
    assert hl.start(A) is True


@pytest.mark.parametrize("kwargs", [{"default_max": -1}, {"per_host": {"a.com": -2}}])
def test_negative_limits_rejected(kwargs):
    with pytest.raises(ValueError):
        HostLimits(**kwargs)