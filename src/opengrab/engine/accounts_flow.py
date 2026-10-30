"""W9: accounts in the running engine - usage limits, cookies, debrid.

AccountPolicy wires five M10 modules that had tests but no caller:

* usage       - daily traffic quota and parallel limit per account
* cookies     - Netscape cookies.txt / Cookie header per account
* accountauth - pick the best usable account (premium first, within quota)
* debrid      - Real-Debrid / AllDebrid unrestrict
* premium     - resolve a hoster link through the first working debrid account

Plugins keep calling ``ctx.account_for(...)``; the manager now hands them an
AccountsView whose find/for_domain go through ``pick_account``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..accountauth import cookie_header_for, matches_domain, pick_account, usage_rule
from ..debrid import service_for
from ..premium import debrid_accounts, premium_resolve
from ..usage import UsageTracker

log = logging.getLogger("opengrab.accounts")

# hosts a debrid service could not unrestrict recently: host -> until
_DEBRID_MISS_TTL = 3600.0


class AccountsView:
    """What plugins see as ``ctx.accounts``: the AccountManager, but account
    lookups respect premium-first, quotas and parallel limits."""

    def __init__(self, manager, policy: "AccountPolicy"):
        self._manager = manager
        self._policy = policy

    def find(self, site: str):
        return self._policy.pick(site)

    def for_domain(self, domain: str):
        return self._policy.pick(domain)

    def __getattr__(self, name):
        return getattr(self._manager, name)


class AccountPolicy:
    def __init__(self, accounts, client, data_dir: Path, settings):
        self.accounts = accounts
        self.client = client
        self.settings = settings
        self.path = Path(data_dir) / "usage.json"
        self._lock = threading.RLock()
        self.active: dict[str, int] = {}
        self._debrid_miss: dict[str, float] = {}
        self._dirty = 0
        try:
            self.tracker = UsageTracker.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, IndexError):
            self.tracker = UsageTracker()

    # -- selection ------------------------------------------------------------
    def pick(self, site_or_host: str):
        host = (site_or_host or "").lower()
        if "://" in host:
            host = urlparse(host).hostname or ""
        with self._lock:
            return pick_account(list(self.accounts.accounts), host, self.tracker, dict(self.active))

    def account(self, account_id: str):
        return next((a for a in self.accounts.accounts if a.id == account_id), None)

    def cookie_header(self, url: str) -> str:
        acc = self.pick(url)
        if acc is None:
            return ""
        return cookie_header_for(acc, url, time.time())

    # -- usage -------------------------------------------------------------------
    def start(self, account_id: str) -> None:
        with self._lock:
            self.active[account_id] = self.active.get(account_id, 0) + 1

    def finish(self, account_id: str) -> None:
        with self._lock:
            n = self.active.get(account_id, 0) - 1
            if n <= 0:
                self.active.pop(account_id, None)
            else:
                self.active[account_id] = n
        self.save(force=True)

    def record(self, account_id: str, nbytes: int) -> None:
        acc = self.account(account_id)
        if acc is None or nbytes <= 0:
            return
        with self._lock:
            self.tracker.record(account_id, nbytes, usage_rule(acc))
            self._dirty += nbytes
        if self._dirty >= 4 * 1024 * 1024:
            self.save()

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not force and self._dirty < 4 * 1024 * 1024:
                return
            self._dirty = 0
            data = self.tracker.to_dict()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".usage-", dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except OSError:
            log.debug("could not save usage.json", exc_info=True)

    def usage(self) -> list[dict]:
        out = []
        with self._lock:
            for a in self.accounts.accounts:
                rule = usage_rule(a)
                svc = service_for(a)
                out.append({"id": a.id, "site": a.site, "used_today": self.tracker.used(a.id, rule),
                            "remaining": self.tracker.remaining(a.id, rule),
                            "daily_bytes": rule.daily_bytes, "max_parallel": rule.max_parallel,
                            "active": self.active.get(a.id, 0),
                            "service": svc.name if svc else ""})
        return out

    # -- debrid --------------------------------------------------------------------
    def _http(self, method: str, url: str, headers: dict, form: dict):
        r = self.client.request(method, url, data=form or None, headers=headers,
                                detect_challenges=False, timeout=30)
        return r.status_code, r.text

    def has_debrid(self) -> bool:
        return bool(debrid_accounts(list(self.accounts.accounts)))

    def debrid_resolve(self, url: str):
        """ResolveResult through a debrid account, or None (never raises)."""
        if not getattr(self.settings, "premium_prefer_debrid", True) or not self.has_debrid():
            return None
        host = (urlparse(url).hostname or "").lower()
        if any(matches_domain(a, host) for a in debrid_accounts(list(self.accounts.accounts))):
            return None  # a link on the debrid service itself
        with self._lock:
            if self._debrid_miss.get(host, 0) > time.time():
                return None
            active = dict(self.active)
        try:
            res = premium_resolve(url, list(self.accounts.accounts), self._http, self.tracker, active)
        except Exception as exc:
            log.info("debrid resolve failed for %s: %s", host, exc)
            res = None
        if res is None:
            with self._lock:
                self._debrid_miss[host] = time.time() + _DEBRID_MISS_TTL
        return res
