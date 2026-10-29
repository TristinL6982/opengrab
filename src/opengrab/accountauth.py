"""Account auth glue (M10c): per-account cookies and usage rules stored in Account.data."""
from __future__ import annotations

from .cookies import Cookie, cookies_for_url, parse_header, parse_netscape, to_header
from .models import Account
from .usage import UsageRule, UsageTracker


def usage_rule(acc: Account) -> UsageRule:
    """UsageRule from acc.data["usage"] (daily_bytes, max_parallel, reset_hour_utc)."""
    raw = (acc.data or {}).get("usage") or {}
    return UsageRule(
        daily_bytes=int(raw.get("daily_bytes", 0) or 0),
        max_parallel=int(raw.get("max_parallel", 0) or 0),
        reset_hour_utc=int(raw.get("reset_hour_utc", 0) or 0),
    )


def account_cookies(acc: Account) -> list[Cookie]:
    """Cookies from acc.data["cookies_txt"] (Netscape text) then acc.data["cookie_header"]."""
    data = acc.data or {}
    out = parse_netscape(str(data.get("cookies_txt") or ""))
    header = str(data.get("cookie_header") or "")
    if header.strip():
        out.extend(parse_header(header, acc.site))
    return out


def cookie_header_for(acc: Account, url: str, now: float) -> str:
    """Cookie header value this account should send for url ("" if none)."""
    return to_header(cookies_for_url(account_cookies(acc), url, now))


def matches_domain(acc: Account, domain: str) -> bool:
    d = (domain or "").lower().lstrip(".")
    s = (acc.site or "").lower().lstrip(".")
    if not d or not s:
        return False
    return s == d or d.endswith("." + s) or s.endswith("." + d)


def pick_account(accounts: list[Account], domain: str, tracker: UsageTracker,
                 active: dict[str, int] | None = None) -> Account | None:
    """First usable account for domain: premium before free, skipping any over quota or parallel limit."""
    active = active or {}
    cands = [a for a in accounts if matches_domain(a, domain)]
    cands.sort(key=lambda a: not a.premium)
    for a in cands:
        if tracker.can_start(a.id, usage_rule(a), active.get(a.id, 0)):
            return a
    return None