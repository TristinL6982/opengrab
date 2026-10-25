"""Site account (login) manager — JD2 'accounts' concept, unpaywalled:
users store their own site credentials; plugins consume them for premium
direct downloads / ad-free pages.  Credentials are stored locally,
optionally obfuscated (not secret-grade; see README for a note on this).
"""
from __future__ import annotations

import base64
import json
import threading

from .models import Account


class AccountManager:
    def __init__(self):
        self.accounts: list[Account] = []
        self._lock = threading.Lock()

    @property
    def _path(self):
        from .settings import app_data_dir
        return app_data_dir() / "accounts.json"

    # -- persistence --------------------------------------------------------
    def load(self) -> None:
        p = self._path
        if p.exists():
            try:
                raw = json.loads(p.read_text("utf-8"))
                self.accounts = [Account(**a) for a in raw]
            except Exception:
                self.accounts = []

    def save(self) -> None:
        with self._lock:
            payload = []
            for a in self.accounts:
                d = a.model_dump()
                if d.get("password"):
                    d["password"] = self._enc(d["password"])
                payload.append(d)
            self._path.write_text(json.dumps(payload, indent=2), "utf-8")

    @staticmethod
    def _enc(s: str) -> str:
        return "ob:" + base64.b64encode(s.encode()).decode()

    @staticmethod
    def _dec(s: str) -> str:
        if s.startswith("ob:"):
            try:
                return base64.b64decode(s[3:].encode()).decode()
            except Exception:
                return s
        return s

    # -- ops -----------------------------------------------------------------
    def add(self, site: str, username: str, password: str = "",
            premium: bool = False, data: dict | None = None) -> Account:
        acc = Account(site=site, username=username, password=password,
                      premium=premium, data=data or {})
        with self._lock:
            self.accounts.append(acc)
        self.save()
        return acc

    def remove(self, account_id: str) -> bool:
        with self._lock:
            before = len(self.accounts)
            self.accounts = [a for a in self.accounts if a.id != account_id]
            changed = len(self.accounts) != before
        if changed:
            self.save()
        return changed

    def update(self, account_id: str, **fields) -> Account | None:
        with self._lock:
            for a in self.accounts:
                if a.id == account_id:
                    for k, v in fields.items():
                        if hasattr(a, k) and v is not None:
                            setattr(a, k, v)
                    self.save()
                    return a
        return None

    def find(self, site: str) -> Account | None:
        """Best account for a site: premium first, then first match."""
        for a in self.accounts:
            if a.site.lower() == site.lower() and a.premium:
                return a
        for a in self.accounts:
            if a.site.lower() == site.lower():
                return a
        return None

    def for_domain(self, domain: str) -> Account | None:
        """Match account.site against a domain or its registrable part."""
        d = domain.lower()
        for a in self.accounts:
            s = a.site.lower()
            if s == d or d.endswith("." + s) or s.endswith("." + d):
                return a
        return None

    def snapshot(self) -> list[dict]:
        return [a.to_public() for a in self.accounts]

    def clear_password_cache(self) -> None:
        pass

    def decrypted_password(self, acc: Account) -> str:
        return self._dec(acc.password)
