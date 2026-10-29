"""M12 access gate: ties pairing, remote policy and auth rate limiting together.

The HTTP server asks this one object whether a request may reach the API
(transport), who is calling (master token or a paired device token) and what
that caller may do. The pairing endpoints are implemented here as plain
methods returning (status, payload) so they can be tested without sockets.
Paired devices are persisted to pairing.json in the data dir.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .pairing import PairingError, PairingStore, format_code
from .remote import (AuthRateLimiter, RemotePolicy, SCOPE_MASTER, bearer,
                     resolve_scope, scope_allows)

PAIRING_FILE = "pairing.json"


class AccessGate:
    def __init__(self, master_token: str, store: Optional[PairingStore] = None,
                 policy: Optional[RemotePolicy] = None,
                 limiter: Optional[AuthRateLimiter] = None,
                 path: Optional[Path] = None):
        self.master_token = master_token or ""
        self.store = store if store is not None else PairingStore()
        self.policy = policy if policy is not None else RemotePolicy()
        self.limiter = limiter if limiter is not None else AuthRateLimiter()
        self.path = Path(path) if path is not None else None

    # ------------------------------------------------------------ persistence
    @classmethod
    def load(cls, data_dir, master_token: str, policy: Optional[RemotePolicy] = None,
             clock: Callable[[], float] = time.time) -> "AccessGate":
        path = Path(data_dir) / PAIRING_FILE
        store = PairingStore(clock=clock)
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    store.load_dict(json.load(fh))
            except (OSError, ValueError):
                pass
        return cls(master_token, store=store, policy=policy,
                   limiter=AuthRateLimiter(clock=clock), path=path)

    def save(self) -> bool:
        if self.path is None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.store.to_dict(), fh, indent=2)
        os.replace(tmp, self.path)
        return True

    # -------------------------------------------------------------- requests
    def client_ip(self, peer_ip, headers) -> str:
        return self.policy.client_ip(peer_ip, headers)

    def transport_allowed(self, peer_ip, headers, is_tls: bool = False) -> bool:
        return self.policy.transport_allowed(peer_ip, headers, is_tls)

    def scope_for(self, headers) -> Optional[str]:
        return resolve_scope(bearer(headers), self.master_token, self.store.authenticate)

    def check(self, peer_ip, headers, action: str = "api") -> tuple[int, str]:
        """Return (200, scope) when allowed, else (401|403|429, error message)."""
        ip = self.client_ip(peer_ip, headers)
        if self.limiter.is_blocked(ip):
            return 429, f"too many failed attempts; retry in {self.limiter.retry_after(ip)} s"
        scope = self.scope_for(headers)
        if scope is None:
            self.limiter.record_failure(ip)
            return 401, "unauthorized: send Bearer token"
        self.limiter.record_success(ip)
        if not scope_allows(scope, action):
            return 403, "this action needs the master token"
        return 200, scope

    # ------------------------------------------------------------- endpoints
    def pair_start(self, scope) -> tuple[int, dict]:
        if scope != SCOPE_MASTER:
            return 403, {"error": "this action needs the master token"}
        code = self.store.start()
        return 200, {"code": format_code(code), "expires_in": int(self.store.code_ttl)}

    def pair_claim(self, peer_ip, headers, body) -> tuple[int, dict]:
        ip = self.client_ip(peer_ip, headers)
        if self.limiter.is_blocked(ip):
            return 429, {"error": "too many failed attempts",
                         "retry_after": self.limiter.retry_after(ip)}
        body = body if isinstance(body, dict) else {}
        try:
            device, token = self.store.claim(body.get("code"), body.get("name") or "Device")
        except PairingError as exc:
            self.limiter.record_failure(ip)
            return 400, {"error": str(exc)}
        self.limiter.record_success(ip)
        self.save()
        return 200, {"device": device.public(), "token": token}

    def devices_list(self) -> tuple[int, dict]:
        return 200, {"devices": [d.public() for d in self.store.devices()]}

    def devices_revoke(self, device_id) -> tuple[int, dict]:
        if not self.store.revoke(device_id):
            return 404, {"error": "device not found"}
        self.save()
        return 200, {"revoked": str(device_id)}