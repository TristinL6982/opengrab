"""Core data models shared by the engine, API and UI."""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}{short}" if prefix else short


class LinkStatus(str, Enum):
    WAITING = "waiting"          # in queue, not started
    RESOLVING = "resolving"      # plugin extracting real download url
    CAPTCHA = "captcha"          # blocked: needs captcha solution (manual or provider)
    LOGIN = "login"              # blocked: needs account/login
    PAYWALL = "paywall"          # blocked: premium/paywalled content
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    FINISHED = "finished"
    ERROR = "error"
    SKIPPED = "skipped"          # filtered out


class Link(BaseModel):
    id: str = Field(default_factory=lambda: new_id("L"))
    url: str
    title: str = ""
    site: str = ""
    filename: str = ""
    size: Optional[int] = None
    status: LinkStatus = LinkStatus.WAITING
    progress: float = 0.0            # 0..1
    downloaded: int = 0              # bytes
    speed: float = 0.0               # B/s (smoothed)
    eta: Optional[float] = None      # seconds
    error: str = ""
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 0                # higher = earlier in queue
    package_id: str = ""
    # blocking-context
    captcha_image: str = ""          # base64 png when status==CAPTCHA
    captcha_kind: str = "text"       # text|image|recaptcha|hcaptcha|turnstile
    login_hint: str = ""             # form html or hint when status==LOGIN
    paywall_note: str = ""           # message when status==PAYWALL
    created: float = Field(default_factory=time.time)
    finished: Optional[float] = None
    extra: dict[str, Any] = Field(default_factory=dict)
    # internal
    _direct_url: Optional[str] = None
    _direct_headers: dict[str, str] = Field(default_factory=dict, repr=False)

    model_config = {"protected_namespaces": ()}

    def to_public(self) -> dict[str, Any]:
        d = self.model_dump(exclude={"_direct_url", "_direct_headers", "captcha_image"})
        d["has_captcha_image"] = bool(self.captcha_image)
        d["direct_url"] = self._direct_url is not None
        return d


class Package(BaseModel):
    id: str = Field(default_factory=lambda: new_id("P"))
    name: str = "New package"
    created: float = Field(default_factory=time.time)


class Account(BaseModel):
    id: str = Field(default_factory=lambda: new_id("A"))
    site: str
    username: str
    password: str = ""
    premium: bool = False
    data: dict[str, Any] = Field(default_factory=dict)   # extra fields per site
    added: float = Field(default_factory=time.time)

    def to_public(self) -> dict[str, Any]:
        d = self.model_dump()
        d["password"] = "•" * min(len(self.password), 12) if self.password else ""
        return d


class FilterRule(BaseModel):
    id: str = Field(default_factory=lambda: new_id("F"))
    pattern: str                     # regex or glob (if use_glob)
    use_glob: bool = False
    kind: str = "exclude"            # include | exclude
    enabled: bool = True
    note: str = ""


class PluginInfo(BaseModel):
    id: str
    name: str
    site: str = ""
    origin: str = "builtin"          # builtin | user | bridge
    supports_captcha: bool = False
    supports_login: bool = False
