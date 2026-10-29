import pytest

from opengrab.recovery import (
    RetryPolicy,
    Validators,
    can_resume,
    policy_from_dict,
    repair_ranges,
    resume_decision,
    resume_headers,
    validators_from_headers,
)


def test_backoff_grows_and_caps():
    policy = RetryPolicy(base_delay=2.0, factor=2.0, max_delay=10.0)
    assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [2.0, 4.0, 8.0, 10.0]
    with pytest.raises(ValueError):
        policy.delay_for(0)


def test_should_retry_rules():
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(1, network_error=True) is True
    assert policy.should_retry(1, status=503) is True
    assert policy.should_retry(1, status=404) is False
    assert policy.should_retry(3, status=503) is False


def test_policy_from_dict_defaults_and_validation():
    assert policy_from_dict(None) == RetryPolicy()
    assert policy_from_dict({"max_attempts": "2", "max_delay": 5}).max_attempts == 2
    with pytest.raises(ValueError):
        policy_from_dict({"max_attempts": 0})
    with pytest.raises(ValueError):
        policy_from_dict({"factor": 0.5})


def test_validators_and_resume_headers():
    v = validators_from_headers({"ETag": ' "abc" ', "Last-Modified": "Wed, 01 Jan 2025", "Content-Length": "100"})
    assert v == Validators(etag='"abc"', last_modified="Wed, 01 Jan 2025", length=100)
    assert resume_headers(v, 40) == {"Range": "bytes=40-", "If-Range": '"abc"'}
    weak = Validators(etag='W/"x"', last_modified="Thu")
    assert resume_headers(weak, 0) == {"Range": "bytes=0-", "If-Range": "Thu"}
    assert resume_headers(Validators(), 5) == {"Range": "bytes=5-"}


def test_can_resume_compares_validators():
    saved = Validators(etag='"a"', length=100)
    assert can_resume(saved, Validators(etag='"a"', length=100)) is True
    assert can_resume(saved, Validators(etag='"b"', length=100)) is False
    assert can_resume(saved, Validators(etag='"a"', length=99)) is False
    assert can_resume(Validators(last_modified="T"), Validators(last_modified="T")) is True
    assert can_resume(Validators(), Validators()) is False


def test_resume_decision():
    saved = Validators(etag='"a"')
    assert resume_decision(saved, Validators(etag='"a"'), 206) == "resume"
    assert resume_decision(saved, Validators(etag='"b"'), 206) == "restart"
    assert resume_decision(saved, Validators(etag='"a"'), 200) == "restart"
    assert resume_decision(saved, Validators(), 403) == "refresh"


def test_repair_ranges_merges_bad_chunks():
    assert repair_ranges(10, 35, [3, 0, 1, 1, 9]) == [(0, 19), (30, 34)]
    assert repair_ranges(10, 35, []) == []
    with pytest.raises(ValueError):
        repair_ranges(0, 10, [0])