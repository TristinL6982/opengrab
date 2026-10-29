"""M10c: account auth glue (cookies and usage rules per account)."""
import pytest

from opengrab.accountauth import account_cookies, cookie_header_for, matches_domain, pick_account, usage_rule
from opengrab.models import Account
from opengrab.usage import UsageRule, UsageTracker

TXT = ".example.com\tTRUE\t/\tTRUE\t0\tsid\tabc\n"


def acc(site, premium=False, **data):
    return Account(site=site, username="u", premium=premium, data=data)


def test_usage_rule_default_and_values():
    assert usage_rule(acc("x.com")) == UsageRule()
    r = usage_rule(acc("x.com", usage={"daily_bytes": "100", "max_parallel": 2, "reset_hour_utc": 5}))
    assert (r.daily_bytes, r.max_parallel, r.reset_hour_utc) == (100, 2, 5)


def test_usage_rule_invalid_raises():
    with pytest.raises(ValueError):
        usage_rule(acc("x.com", usage={"reset_hour_utc": 24}))


def test_account_cookies_combines_file_and_header():
    cs = account_cookies(acc("Example.com", cookies_txt=TXT, cookie_header="k=v; z=1"))
    assert [c.name for c in cs] == ["sid", "k", "z"]
    assert cs[1].domain == "example.com"


def test_account_cookies_empty():
    assert account_cookies(acc("x.com")) == []


def test_cookie_header_for_url():
    a = acc("example.com", cookies_txt=TXT, cookie_header="k=v")
    assert cookie_header_for(a, "https://dl.example.com/f", now=0) == "sid=abc; k=v"
    assert cookie_header_for(a, "http://dl.example.com/f", now=0) == "k=v"
    assert cookie_header_for(a, "https://other.org/", now=0) == ""


def test_matches_domain():
    a = acc("example.com")
    assert matches_domain(a, "www.example.com") is True
    assert matches_domain(a, "EXAMPLE.com") is True
    assert matches_domain(a, "notexample.com") is False
    assert matches_domain(a, "") is False


def test_pick_account_premium_first_then_quota():
    free = acc("example.com")
    prem = acc("example.com", premium=True, usage={"daily_bytes": 10})
    other = acc("other.org", premium=True)
    t = UsageTracker(clock=lambda: 1000.0)
    accounts = [free, other, prem]
    assert pick_account(accounts, "www.example.com", t) is prem
    t.record(prem.id, 10, usage_rule(prem))
    assert pick_account(accounts, "www.example.com", t) is free


def test_pick_account_parallel_limit_and_none():
    a = acc("example.com", usage={"max_parallel": 1})
    t = UsageTracker(clock=lambda: 0.0)
    assert pick_account([a], "example.com", t, active={a.id: 0}) is a
    assert pick_account([a], "example.com", t, active={a.id: 1}) is None
    assert pick_account([a], "nothing.net", t) is None