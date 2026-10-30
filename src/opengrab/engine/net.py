"""W7: proxies with rules and failover, and server/proxy logins.

NetPolicy joins two M11 modules that had tests but no caller:

* proxies.ProxyList  - an ordered list (plus the old single ``proxy`` setting
  and, optionally, the Windows/OS system proxy), per-host rules ("direct" or
  a proxy), a bypass list, and failover when a proxy cannot be reached.
* siteauth.CredentialStore - per-host-pattern logins for servers and proxies,
  secrets protected with Windows DPAPI (the AccountManager helpers).

HttpClient and LinkDownload ask ``session_kwargs(url)`` for the curl_cffi
options of one request: proxy, proxy login, server login and the libcurl
HTTPAUTH bitmask. libcurl then answers Basic, Digest, NTLM or Negotiate
challenges itself.
"""
from __future__ import annotations

import logging
import threading
import urllib.request
from typing import Optional

from ..proxies import Proxy, ProxyList, parse_proxy
from ..siteauth import CredentialStore

log = logging.getLogger("opengrab.net")

# libcurl CURLAUTH_* bits
CURLAUTH = {"basic": 1, "digest": 2, "negotiate": 4, "kerberos": 4, "ntlm": 8, "any": 1 | 2 | 4 | 8}


def _protect(text: str) -> str:
    from ..accounts import AccountManager
    return AccountManager._enc(text)


def _unprotect(text: str) -> str:
    from ..accounts import AccountManager
    return AccountManager._dec(text)


def system_proxies() -> list[str]:
    """The OS proxy (Windows Internet Options, macOS settings, *_proxy env)."""
    found = []
    for scheme, url in (urllib.request.getproxies() or {}).items():
        if scheme in ("http", "https") and url and url not in found:
            found.append(url)
    return found


class NetPolicy:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.RLock()
        self.errors: list[str] = []
        self.proxy_list = ProxyList()
        self.creds = CredentialStore(protect=_protect, unprotect=_unprotect)
        self.reload()

    # -- config -----------------------------------------------------------
    def reload(self) -> list[str]:
        s = self.settings
        errors: list[str] = []
        raw = []
        if getattr(s, "proxy", ""):
            raw.append(s.proxy)
        raw.extend(getattr(s, "proxies", None) or [])
        if getattr(s, "proxy_use_system", False):
            raw.extend(system_proxies())
        proxies, seen = [], set()
        for text in raw:
            try:
                p = parse_proxy(text)
            except ValueError as exc:
                errors.append(f"proxy {text!r}: {exc}")
                continue
            if p not in seen:
                seen.add(p)
                proxies.append(p)
        plist = ProxyList(proxies, bypass=getattr(s, "proxy_bypass", None) or [])
        for pattern, target in (getattr(s, "proxy_rules", None) or {}).items():
            try:
                plist.add_rule(pattern, target)
            except ValueError as exc:
                errors.append(f"proxy rule {pattern!r}: {exc}")
        creds = CredentialStore.from_list(getattr(s, "site_credentials", None) or [],
                                          protect=_protect, unprotect=_unprotect)
        with self._lock:
            self.proxy_list, self.creds, self.errors = plist, creds, errors
        return errors

    def save_credentials(self) -> None:
        with self._lock:
            self.settings.site_credentials = self.creds.to_list()
        try:
            self.settings.save()
        except Exception:
            log.warning("could not save site logins", exc_info=True)

    # -- proxies ------------------------------------------------------------
    def proxy_for(self, url: str) -> Optional[Proxy]:
        with self._lock:
            try:
                return self.proxy_list.choose(url)
            except LookupError:
                # every list proxy failed: start over rather than give up
                self.proxy_list.reset()
                try:
                    return self.proxy_list.choose(url)
                except LookupError:
                    return None

    def candidates(self, url: str) -> list:
        """Proxies to try in order for url ([None] = connect directly)."""
        with self._lock:
            first = self.proxy_for(url)
            if first is None:
                return [None]
            rest = [p for p in self.proxy_list.candidates(url) if p != first]
            return [first] + rest

    def proxy_failed(self, proxy: Optional[Proxy]) -> None:
        if proxy is not None:
            with self._lock:
                self.proxy_list.mark_failed(proxy)
            log.info("proxy %s:%s failed; trying the next one", proxy.host, proxy.port)

    def proxy_ok(self, proxy: Optional[Proxy]) -> None:
        if proxy is not None:
            with self._lock:
                self.proxy_list.mark_ok(proxy)

    # -- logins + curl options ----------------------------------------------
    def session_kwargs(self, url: str, proxy="auto") -> dict:
        """curl_cffi request keyword arguments for one URL."""
        if proxy == "auto":
            proxy = self.proxy_for(url)
        kw: dict = {}
        curl_opts: dict = {}
        from curl_cffi.const import CurlOpt
        if proxy is not None:
            # credentials go separately so they never appear in a logged URL
            bare = Proxy(proxy.scheme, proxy.host, proxy.port)
            kw["proxies"] = {"http": bare.url, "https": bare.url}
            user, password, scheme = proxy.username, proxy.password, "any"
            if not user:
                stored = self.creds.auth_for(proxy.host, for_proxy=True)
                if stored:
                    user, password, scheme = stored["username"], stored["password"], stored["scheme"]
            if user:
                kw["proxy_auth"] = (user, password)
                curl_opts[CurlOpt.PROXYAUTH] = CURLAUTH.get(scheme, CURLAUTH["any"])
        stored = self.creds.auth_for(url)
        if stored:
            kw["auth"] = (stored["username"], stored["password"])
            curl_opts[CurlOpt.HTTPAUTH] = CURLAUTH.get(stored["scheme"], CURLAUTH["any"])
        if curl_opts:
            kw["curl_options"] = curl_opts
        kw["_proxy"] = proxy
        return kw

    def logins(self) -> list[dict]:
        with self._lock:
            return [{"pattern": c.pattern, "username": c.username, "scheme": c.scheme,
                     "for_proxy": c.for_proxy} for c in self.creds.items]
