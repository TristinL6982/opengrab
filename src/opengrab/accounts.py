"""Site account (login) manager — JD2 'accounts' concept, unpaywalled:
users store their own site credentials; plugins consume them for premium
direct downloads / ad-free pages.  Credentials are stored locally,
optionally obfuscated (not secret-grade; see README for a note on this).
"""
from __future__ import annotations

import base64
import json
import os
import threading
import sys

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
                self.accounts = []
                for a in raw:
                    acc = Account(**a)
                    # Unwrap repeatedly while the result still starts with "ob:" or "dp:"
                    password = acc.password
                    for _ in range(5):  # max 5 times to handle old double-encoded files
                        new_password = self._dec(password)
                        if new_password == password:
                            break
                        password = new_password
                    acc.password = password
                    self.accounts.append(acc)
            except Exception:
                self.accounts = []

    def save(self) -> None:
        with self._lock:
            # Write to temporary file first for atomicity
            tmp_path = self._path.with_suffix(".tmp")
            payload = []
            for a in self.accounts:
                d = a.model_dump()
                if d.get("password"):
                    d["password"] = self._enc(d["password"])
                payload.append(d)
            tmp_path.write_text(json.dumps(payload, indent=2), "utf-8")
            # Atomic replace
            os.replace(tmp_path, self._path)

    @staticmethod
    def _dpapi_call(data: bytes, protect: bool) -> bytes:
        """CryptProtectData / CryptUnprotectData via ctypes (Windows only)."""
        if sys.platform != "win32":
            raise OSError("DPAPI only available on Windows")
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        fn = (ctypes.windll.crypt32.CryptProtectData if protect
              else ctypes.windll.crypt32.CryptUnprotectData)
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1,
                ctypes.byref(blob_out))
        if not ok:
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    @staticmethod
    def _dpapi_protect(data: bytes) -> bytes:
        return AccountManager._dpapi_call(data, True)

    @staticmethod
    def _dpapi_unprotect(data: bytes) -> bytes:
        return AccountManager._dpapi_call(data, False)

    @staticmethod
    def _enc(s: str) -> str:
        """Encode password using DPAPI on Windows or old obfuscation otherwise"""
        if not s:
            return ""
        try:
            if sys.platform == "win32":
                return "dp:" + base64.b64encode(AccountManager._dpapi_protect(s.encode("utf-8"))).decode()
            else:
                return "ob:" + base64.b64encode(s.encode()).decode()
        except Exception:
            # If DPAPI fails on Windows, fall back to old method
            if sys.platform == "win32":
                return "ob:" + base64.b64encode(s.encode()).decode()
            else:
                raise

    @staticmethod
    def _dec(s: str) -> str:
        """Decode password using DPAPI or old obfuscation"""
        if s.startswith("dp:"):
            try:
                data = base64.b64decode(s[3:].encode())
                return AccountManager._dpapi_unprotect(data).decode("utf-8")
            except Exception:
                return ""
        elif s.startswith("ob:"):
            try:
                return base64.b64decode(s[3:].encode()).decode()
            except Exception:
                return ""
        else:
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
        result = []
        for a in self.accounts:
            d = a.to_public()
            # Replace password with empty string and add has_password flag
            d["password"] = ""
            d["has_password"] = bool(a.password)
            result.append(d)
        return result

    def clear_password_cache(self) -> None:
        pass

    def decrypted_password(self, acc: Account) -> str:
        return acc.password
