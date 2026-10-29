"""Resume and recovery helpers (M11p).

Pure logic for auto-retry with exponential backoff, re-validating a partial
download with ETag/Last-Modified before resuming, and planning a repair that
re-downloads only the chunks whose hash check failed.
"""
from __future__ import annotations

from dataclasses import dataclass

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 2.0
    factor: float = 2.0
    max_delay: float = 300.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before retry number `attempt` (1-based)."""
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        delay = self.base_delay * (self.factor ** (attempt - 1))
        return float(min(delay, self.max_delay))

    def should_retry(self, attempt: int, status: int | None = None, network_error: bool = False) -> bool:
        """True when attempt number `attempt` failed and another try is allowed."""
        if attempt >= self.max_attempts:
            return False
        if network_error:
            return True
        return status in RETRYABLE_STATUS


def policy_from_dict(data) -> RetryPolicy:
    data = data or {}
    policy = RetryPolicy(
        max_attempts=int(data.get("max_attempts", 5)),
        base_delay=float(data.get("base_delay", 2.0)),
        factor=float(data.get("factor", 2.0)),
        max_delay=float(data.get("max_delay", 300.0)),
    )
    if policy.max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if policy.base_delay < 0 or policy.max_delay < 0:
        raise ValueError("delays must be >= 0")
    if policy.factor < 1:
        raise ValueError("factor must be >= 1")
    return policy


@dataclass
class Validators:
    etag: str = ""
    last_modified: str = ""
    length: int | None = None


def validators_from_headers(headers) -> Validators:
    lower = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    length = lower.get("content-length")
    return Validators(
        etag=lower.get("etag", "").strip(),
        last_modified=lower.get("last-modified", "").strip(),
        length=int(length) if length and length.strip().isdigit() else None,
    )


def resume_headers(saved: Validators, offset: int) -> dict:
    """Headers for a conditional range request resuming at `offset`."""
    headers = {"Range": f"bytes={offset}-"}
    if saved.etag and not saved.etag.startswith("W/"):
        headers["If-Range"] = saved.etag
    elif saved.last_modified:
        headers["If-Range"] = saved.last_modified
    return headers


def can_resume(saved: Validators, current: Validators) -> bool:
    """True when the remote file is still the one we partially downloaded."""
    if saved.length is not None and current.length is not None and saved.length != current.length:
        return False
    if saved.etag and current.etag:
        return saved.etag == current.etag
    if saved.last_modified and current.last_modified:
        return saved.last_modified == current.last_modified
    return False


def resume_decision(saved: Validators, current: Validators, status: int) -> str:
    """'resume', 'restart' or 'refresh' for a resume attempt's response status."""
    if status in (401, 403, 404, 410):
        return "refresh"
    if status == 200:
        return "restart"
    if status == 206 and can_resume(saved, current):
        return "resume"
    return "restart"


def repair_ranges(chunk_size: int, total: int, bad_chunks) -> list:
    """Merge bad chunk indexes into (start, end) inclusive byte ranges."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    last_index = (total - 1) // chunk_size if total > 0 else -1
    indexes = sorted({int(i) for i in bad_chunks if 0 <= int(i) <= last_index})
    ranges = []
    for i in indexes:
        start = i * chunk_size
        end = min(start + chunk_size, total) - 1
        if ranges and ranges[-1][1] + 1 == start:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    return ranges