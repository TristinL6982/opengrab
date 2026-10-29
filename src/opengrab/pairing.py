"""M12 device-code pairing for remote, mobile, extension and agent clients.

The desktop user starts pairing and gets a short one-time code. A new device
claims that code (once, before it expires) and receives its own device token.
Only the SHA-256 hash of each device token is stored, and every device can be
revoked on its own. Too many wrong codes cancel all pending codes.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
DEFAULT_CODE_TTL = 300.0
MAX_FAILED_CLAIMS = 5
MAX_NAME_LENGTH = 64


class PairingError(Exception):
    """A pairing code was invalid, expired or locked out."""


def _random_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _random_token() -> str:
    return secrets.token_urlsafe(32)


def normalize_code(code) -> str:
    if not isinstance(code, str):
        raise PairingError("pairing code must be text")
    cleaned = "".join(ch for ch in code.upper() if ch not in " -")
    if len(cleaned) != CODE_LENGTH or any(ch not in CODE_ALPHABET for ch in cleaned):
        raise PairingError("invalid or expired pairing code")
    return cleaned


def format_code(code: str) -> str:
    c = normalize_code(code)
    half = CODE_LENGTH // 2
    return f"{c[:half]}-{c[half:]}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_name(name) -> str:
    text = name.strip() if isinstance(name, str) else ""
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_NAME_LENGTH] or "Device"


@dataclass
class Device:
    id: str
    name: str
    token_hash: str
    created: float
    last_seen: Optional[float] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "token_hash": self.token_hash,
                "created": self.created, "last_seen": self.last_seen}

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        return cls(id=str(data["id"]), name=clean_name(data.get("name")),
                   token_hash=str(data["token_hash"]),
                   created=float(data.get("created", 0.0)),
                   last_seen=(None if data.get("last_seen") is None
                              else float(data["last_seen"])))

    def public(self) -> dict:
        """Device info that is safe to show in the UI (no token hash)."""
        return {"id": self.id, "name": self.name, "created": self.created,
                "last_seen": self.last_seen}


class PairingStore:
    def __init__(self, clock: Callable[[], float] = time.time,
                 code_factory: Callable[[], str] = _random_code,
                 token_factory: Callable[[], str] = _random_token,
                 code_ttl: float = DEFAULT_CODE_TTL,
                 max_failed: int = MAX_FAILED_CLAIMS):
        self._clock = clock
        self._code_factory = code_factory
        self._token_factory = token_factory
        self.code_ttl = float(code_ttl)
        self.max_failed = int(max_failed)
        self._pending: dict[str, float] = {}
        self._failed = 0
        self._devices: dict[str, Device] = {}

    def _purge(self) -> None:
        now = self._clock()
        for code in [c for c, exp in self._pending.items() if exp <= now]:
            del self._pending[code]

    def start(self) -> str:
        """Create a new one-time pairing code and return it (normalized)."""
        self._purge()
        code = normalize_code(self._code_factory())
        self._pending[code] = self._clock() + self.code_ttl
        self._failed = 0
        return code

    def pending_count(self) -> int:
        self._purge()
        return len(self._pending)

    def claim(self, code, name="Device") -> tuple[Device, str]:
        """Exchange a pending code for a new device and its plain token."""
        self._purge()
        try:
            key = normalize_code(code)
        except PairingError:
            key = None
        if key is None or key not in self._pending:
            self._failed += 1
            if self._failed >= self.max_failed:
                self._pending.clear()
            raise PairingError("invalid or expired pairing code")
        del self._pending[key]
        token = self._token_factory()
        digest = hash_token(token)
        device = Device(id=digest[:12], name=clean_name(name), token_hash=digest,
                        created=self._clock())
        self._devices[device.id] = device
        return device, token

    def authenticate(self, token) -> Optional[Device]:
        if not isinstance(token, str) or not token:
            return None
        digest = hash_token(token)
        found = None
        for device in self._devices.values():
            if hmac.compare_digest(device.token_hash, digest):
                found = device
        if found is not None:
            found.last_seen = self._clock()
        return found

    def revoke(self, device_id) -> bool:
        return self._devices.pop(str(device_id), None) is not None

    def rename(self, device_id, name) -> bool:
        device = self._devices.get(str(device_id))
        if device is None:
            return False
        device.name = clean_name(name)
        return True

    def devices(self) -> list[Device]:
        return sorted(self._devices.values(), key=lambda d: (d.created, d.id))

    def to_dict(self) -> dict:
        return {"devices": [d.to_dict() for d in self.devices()]}

    def load_dict(self, data) -> list[str]:
        """Replace devices from saved data; return the ids of skipped bad entries."""
        self._devices = {}
        skipped: list[str] = []
        items = data.get("devices") if isinstance(data, dict) else None
        for index, item in enumerate(items if isinstance(items, list) else []):
            try:
                device = Device.from_dict(item)
            except (KeyError, TypeError, ValueError, AttributeError):
                skipped.append(str(index))
                continue
            self._devices[device.id] = device
        return skipped