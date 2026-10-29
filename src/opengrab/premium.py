"""Premium paths (M10e): resolve a hoster link through the first usable debrid account."""
from __future__ import annotations

from .accountauth import usage_rule
from .debrid import DebridError, Http, service_for, unrestrict_with
from .models import Account
from .plugins.base import ResolveResult


def debrid_accounts(accounts: list[Account]) -> list[Account]:
    """Accounts that map to a debrid service, premium first, otherwise in the given order."""
    out = [a for a in accounts if service_for(a) is not None]
    out.sort(key=lambda a: not a.premium)
    return out


def premium_resolve(url: str, accounts: list[Account], http: Http, tracker=None,
                    active: dict[str, int] | None = None) -> ResolveResult | None:
    """Unrestrict url with the first debrid account the tracker allows (tracker None allows all)
    whose service succeeds; accounts raising DebridError are skipped. None if nothing works."""
    active = active or {}
    for a in debrid_accounts(accounts):
        if tracker is not None and not tracker.can_start(a.id, usage_rule(a), active.get(a.id, 0)):
            continue
        try:
            link = unrestrict_with(a, url, http)
        except DebridError:
            continue
        return ResolveResult(url=link.url, filename=link.filename, size=link.size or None,
                             extra={"account": a.id, "service": service_for(a).name})
    return None