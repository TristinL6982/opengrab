"""Proxy servers (M11c): parse proxy URLs, per-host rules, a bypass list and failover."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from opengrab.hostlimits import host_of

SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
DEFAULT_PORTS = {"http": 8080, "https": 443, "socks4": 1080, "socks4a": 1080, "socks5": 1080, "socks5h": 1080}


@dataclass(frozen=True)
class Proxy:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def remote_dns(self) -> bool:
        """True when the proxy resolves host names itself (socks4a, socks5h)."""
        return self.scheme in ("socks4a", "socks5h")

    @property
    def url(self) -> str:
        auth = ""
        if self.username:
            auth = quote(self.username, safe="")
            if self.password:
                auth += ":" + quote(self.password, safe="")
            auth += "@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


def parse_proxy(text: str) -> Proxy:
    """Parse 'scheme://user:pass@host:port'; scheme defaults to http, port to the scheme default."""
    s = (text or "").strip()
    if not s:
        raise ValueError("empty proxy")
    if "://" not in s:
        s = "http://" + s
    parts = urlsplit(s)
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise ValueError(f"unsupported proxy scheme: {scheme}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("proxy host missing")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("bad proxy port") from exc
    if port is None:
        port = DEFAULT_PORTS[scheme]
    if port == 0:
        raise ValueError("bad proxy port")
    return Proxy(scheme, host, port, unquote(parts.username or ""), unquote(parts.password or ""))


def host_matches(pattern: str, host: str) -> bool:
    """'*' matches all, '*.example.com' matches example.com and its subdomains, else exact (www. ignored)."""
    p = pattern.strip().lower()
    if p.startswith("www."):
        p = p[4:]
    h = host.strip().lower()
    if p == "*":
        return True
    if p.startswith("*."):
        base = p[2:]
        return h == base or h.endswith("." + base)
    return h == p


class ProxyList:
    """Ordered proxies with per-host rules, a bypass list and failover."""

    def __init__(self, proxies=(), rules: dict | None = None, bypass=()):
        self.proxies = [p if isinstance(p, Proxy) else parse_proxy(p) for p in proxies]
        self.rules: list[tuple[str, Proxy | None]] = []
        for pattern, target in (rules or {}).items():
            self.add_rule(pattern, target)
        self.bypass = [b.strip().lower() for b in bypass if b.strip()]
        self._failed: set[Proxy] = set()

    def add_rule(self, pattern: str, target) -> None:
        """target is a proxy (str or Proxy), or None / 'direct' for no proxy. First matching rule wins."""
        if target is None or (isinstance(target, str) and target.strip().lower() == "direct"):
            proxy = None
        else:
            proxy = target if isinstance(target, Proxy) else parse_proxy(target)
        self.rules.append((pattern, proxy))

    def _route(self, url: str):
        host = host_of(url)
        if any(host_matches(b, host) for b in self.bypass):
            return "direct", None
        for pattern, proxy in self.rules:
            if host_matches(pattern, host):
                return ("direct", None) if proxy is None else ("rule", proxy)
        return ("list", None) if self.proxies else ("direct", None)

    def candidates(self, url: str) -> list[Proxy]:
        kind, proxy = self._route(url)
        if kind == "direct":
            return []
        if kind == "rule":
            return [proxy]
        return [p for p in self.proxies if p not in self._failed]

    def choose(self, url: str) -> Proxy | None:
        """Proxy to use for url, or None to connect directly. Raises LookupError if every list proxy failed."""
        kind, proxy = self._route(url)
        if kind == "direct":
            return None
        if kind == "rule":
            return proxy
        live = self.candidates(url)
        if not live:
            raise LookupError("all proxies failed")
        return live[0]

    def mark_failed(self, proxy: Proxy) -> None:
        self._failed.add(proxy)

    def mark_ok(self, proxy: Proxy) -> None:
        self._failed.discard(proxy)

    def reset(self) -> None:
        self._failed.clear()