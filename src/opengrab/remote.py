"""M12 remote access policy for the local API.

Decides whether a request may reach the API at all (transport) and what it
may do (scope). Loopback is always allowed. Other clients need HTTPS, either
directly or through a trusted reverse proxy that sets X-Forwarded-Proto, unless
the user opted in to plain remote HTTP (Docker/LAN). The master token has full
access; device tokens from pairing get the "device" scope. Failed logins are
rate limited per client IP with an injected clock.
"""
from __future__ import annotations

import hmac
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

SCOPE_MASTER = "master"
SCOPE_DEVICE = "device"
MASTER_ONLY_ACTIONS = frozenset({"pair_start", "devices_list", "devices_revoke", "settings_write"})
DEFAULT_MAX_FAILURES = 10
DEFAULT_WINDOW = 60.0
DEFAULT_BLOCK = 300.0


def parse_ip(value) -> Optional[ipaddress._BaseAddress]:
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def is_loopback(value) -> bool:
    ip = parse_ip(value)
    return bool(ip is not None and ip.is_loopback)


def ip_in_list(value, networks) -> bool:
    ip = parse_ip(value)
    if ip is None:
        return False
    for item in networks or ():
        try:
            net = ipaddress.ip_network(str(item).strip(), strict=False)
        except ValueError:
            continue
        if ip.version == net.version and ip in net:
            return True
    return False


def forwarded_proto(headers) -> str:
    proto = (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    if proto:
        return proto
    for part in (headers.get("Forwarded") or "").split(";"):
        part = part.strip()
        if part.lower().startswith("proto="):
            return part.split("=", 1)[1].strip(' "').lower()
    return ""


def forwarded_client(headers) -> str:
    return (headers.get("X-Forwarded-For") or "").split(",")[0].strip()


@dataclass
class RemotePolicy:
    allow_remote_http: bool = False
    trusted_proxies: list = field(default_factory=list)

    def transport_allowed(self, client_ip, headers, is_tls: bool = False) -> bool:
        if is_tls:
            return True
        proxied = is_loopback(client_ip) or ip_in_list(client_ip, self.trusted_proxies)
        has_forward = bool(headers.get("X-Forwarded-For") or headers.get("Forwarded")
                           or headers.get("X-Forwarded-Proto"))
        if proxied and has_forward:
            if forwarded_proto(headers) == "https":
                return True
            return self.allow_remote_http
        if is_loopback(client_ip):
            return True
        return self.allow_remote_http

    def client_ip(self, peer_ip, headers) -> str:
        """Real client IP: the forwarded one only when the peer is a trusted proxy."""
        if is_loopback(peer_ip) or ip_in_list(peer_ip, self.trusted_proxies):
            fwd = forwarded_client(headers)
            if parse_ip(fwd) is not None:
                return fwd
        return str(peer_ip)


def policy_from_dict(data) -> RemotePolicy:
    if not isinstance(data, dict):
        return RemotePolicy()
    proxies = data.get("trusted_proxies") or []
    if not isinstance(proxies, list):
        proxies = []
    return RemotePolicy(allow_remote_http=data.get("allow_remote_http") is True,
                        trusted_proxies=[str(p) for p in proxies if isinstance(p, str)])


def bearer(headers) -> str:
    h = (headers.get("Authorization") or "").strip()
    if h[:7].lower() == "bearer ":
        return h[7:].strip()
    return ""


def resolve_scope(token, master_token, authenticate_device: Callable) -> Optional[str]:
    """Return SCOPE_MASTER, SCOPE_DEVICE or None for a presented token."""
    if not token:
        return None
    if master_token and hmac.compare_digest(str(token), str(master_token)):
        return SCOPE_MASTER
    if authenticate_device is not None and authenticate_device(token) is not None:
        return SCOPE_DEVICE
    return None


def scope_allows(scope, action) -> bool:
    if scope == SCOPE_MASTER:
        return True
    if scope == SCOPE_DEVICE:
        return action not in MASTER_ONLY_ACTIONS
    return False


class AuthRateLimiter:
    """Blocks an IP for block seconds after max_failures failures within window."""

    def __init__(self, max_failures: int = DEFAULT_MAX_FAILURES, window: float = DEFAULT_WINDOW,
                 block: float = DEFAULT_BLOCK, clock: Callable[[], float] = time.time):
        self.max_failures = int(max_failures)
        self.window = float(window)
        self.block = float(block)
        self.clock = clock
        self._fails: dict = {}
        self._blocked: dict = {}

    def is_blocked(self, ip) -> bool:
        until = self._blocked.get(str(ip))
        if until is None:
            return False
        if self.clock() >= until:
            del self._blocked[str(ip)]
            return False
        return True

    def record_failure(self, ip) -> bool:
        """Record a failure; return True when the IP is now blocked."""
        key = str(ip)
        now = self.clock()
        recent = [t for t in self._fails.get(key, []) if now - t < self.window]
        recent.append(now)
        self._fails[key] = recent
        if len(recent) >= self.max_failures:
            self._blocked[key] = now + self.block
            self._fails[key] = []
            return True
        return False

    def record_success(self, ip) -> None:
        self._fails.pop(str(ip), None)

    def retry_after(self, ip) -> int:
        until = self._blocked.get(str(ip))
        if until is None:
            return 0
        return max(0, int(until - self.clock() + 0.999))