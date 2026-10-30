"""Captcha inbox: challenges waiting for a human to solve them in the UI."""
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional


@dataclass
class CaptchaRequest:
    id: str
    host: str
    kind: str
    sitekey: Optional[str]
    page_url: str
    created_at: float
    timeout_s: float = 300.0
    status: str = "pending"
    answer: Optional[str] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return now >= self.created_at + self.timeout_s

    def to_dict(self) -> dict:
        return asdict(self)


class InboxFull(Exception):
    """Raised when max_pending pending requests already exist."""


class CaptchaInbox:
    def __init__(self, max_pending: int = 50,
                 on_new: Optional[Callable[["CaptchaRequest"], None]] = None):
        self._max_pending = max_pending
        # push hook: called once per NEW request (not for de-duplicated ones),
        # outside the lock, so paired/remote devices can be told a captcha waits
        self.on_new = on_new
        self._requests: Dict[str, CaptchaRequest] = {}
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    # --- internal helpers (caller holds the lock) ---
    def _expire(self, now: float) -> None:
        changed = False
        for r in self._requests.values():
            if r.status == "pending" and r.is_expired(now):
                r.status = "expired"
                changed = True
        if changed:
            self._cond.notify_all()

    def _live(self, now: float) -> List[CaptchaRequest]:
        self._expire(now)
        return [r for r in self._requests.values() if r.status == "pending"]

    # --- public API ---
    def submit(self, host: str, kind: str, page_url: str, sitekey: Optional[str] = None,
               timeout_s: float = 300.0, now: Optional[float] = None) -> CaptchaRequest:
        if now is None:
            now = time.time()
        with self._lock:
            live = self._live(now)
            for r in live:
                if r.host == host and r.kind == kind:
                    return r
            if len(live) >= self._max_pending:
                raise InboxFull(f"captcha inbox full ({self._max_pending} pending)")
            req = CaptchaRequest(id=uuid.uuid4().hex, host=host, kind=kind, sitekey=sitekey,
                                 page_url=page_url, created_at=now, timeout_s=timeout_s)
            self._requests[req.id] = req
        hook = self.on_new
        if hook is not None:
            try:
                hook(req)
            except Exception:
                pass  # a broken push target must never block the download
        return req

    def pending(self, now: Optional[float] = None) -> List[CaptchaRequest]:
        if now is None:
            now = time.time()
        with self._lock:
            return sorted(self._live(now), key=lambda r: r.created_at)

    def get(self, id: str) -> Optional[CaptchaRequest]:
        with self._lock:
            return self._requests.get(id)

    def solve(self, id: str, answer: str) -> bool:
        with self._lock:
            self._expire(time.time())
            r = self._requests.get(id)
            if r is None or r.status != "pending" or not answer:
                return False
            r.answer = answer
            r.status = "solved"
            self._cond.notify_all()
            return True

    def skip(self, id: str) -> bool:
        with self._lock:
            self._expire(time.time())
            r = self._requests.get(id)
            if r is None or r.status != "pending":
                return False
            r.status = "skipped"
            self._cond.notify_all()
            return True

    def wait(self, id: str, timeout: float) -> Optional[CaptchaRequest]:
        deadline = time.monotonic() + timeout
        with self._lock:
            r = self._requests.get(id)
            if r is None:
                return None
            while True:
                self._expire(time.time())
                if r.status != "pending":
                    return r
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return r
                # wake at least by the request's own expiry so status can flip
                until_expiry = max(0.0, r.created_at + r.timeout_s - time.time())
                self._cond.wait(min(remaining, until_expiry + 0.001))

    def purge(self, now: Optional[float] = None) -> int:
        if now is None:
            now = time.time()
        with self._lock:
            self._expire(now)
            gone = [k for k, r in self._requests.items() if r.status != "pending"]
            for k in gone:
                del self._requests[k]
            return len(gone)

    def __len__(self) -> int:
        with self._lock:
            return len(self._live(time.time()))