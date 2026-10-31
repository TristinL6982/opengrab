"""VirusTotal check for finished downloads (API v3, your own free API key).

By default only the file's SHA-256 is sent: VirusTotal answers with the
verdicts of its ~70 engines if anyone has submitted that file before. Files
VirusTotal has never seen are uploaded only when the user turns that on,
because an upload shares the file with VirusTotal and its partners.

The HTTP function is injected so the module is testable without the network.
The API key is sent only to www.virustotal.com and never logged.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

API = "https://www.virustotal.com/api/v3"
GUI = "https://www.virustotal.com/gui/file/"
UPLOAD_LIMIT = 32 * 1024 * 1024  # the plain /files endpoint; bigger files need a special URL

CLEAN = "clean"
FLAGGED = "flagged"
UNKNOWN = "unknown"      # VirusTotal has no report and uploading is off
QUEUED = "queued"        # uploaded, analysis not finished in time
ERROR = "error"

# http(method, url, headers, body_or_None, timeout) -> (status, bytes)
Http = Callable[[str, str, dict, Optional[bytes], float], "tuple[int, bytes]"]


@dataclass
class VtConfig:
    enabled: bool = False          # check every finished download
    api_key: str = ""
    upload_unknown: bool = False   # upload files VirusTotal has never seen
    min_detections: int = 2        # engines that must agree before a file counts as flagged
    timeout_s: float = 120.0

    def to_dict(self, reveal_key: bool = False) -> dict:
        return {"enabled": self.enabled, "api_key": self.api_key if reveal_key else ("set" if self.api_key else ""),
                "upload_unknown": self.upload_unknown, "min_detections": self.min_detections,
                "timeout_s": self.timeout_s}


def config_from_dict(d: Optional[dict]) -> VtConfig:
    d = d or {}
    return VtConfig(enabled=bool(d.get("enabled", False)), api_key=str(d.get("api_key", "") or "").strip(),
                    upload_unknown=bool(d.get("upload_unknown", False)),
                    min_detections=max(1, int(d.get("min_detections", 2) or 1)),
                    timeout_s=max(10.0, float(d.get("timeout_s", 120.0) or 120.0)))


@dataclass
class VtResult:
    status: str
    sha256: str = ""
    malicious: int = 0
    suspicious: int = 0
    engines: int = 0
    detail: str = ""
    names: list = field(default_factory=list)  # a few engines' labels when flagged

    @property
    def flagged(self) -> bool:
        return self.status == FLAGGED

    @property
    def link(self) -> str:
        return GUI + self.sha256 if self.sha256 else ""

    def to_dict(self) -> dict:
        return {"status": self.status, "sha256": self.sha256, "malicious": self.malicious,
                "suspicious": self.suspicious, "engines": self.engines, "detail": self.detail,
                "names": list(self.names), "link": self.link}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _default_http(method: str, url: str, headers: dict, body: Optional[bytes], timeout: float):
    import urllib.error
    import urllib.request
    from .tls import urlopen
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


class VirusTotal:
    def __init__(self, config: VtConfig, http: Http = _default_http, sleep=time.sleep):
        self.config = config
        self.http = http
        self.sleep = sleep

    def _call(self, method: str, url: str, body: Optional[bytes] = None, ctype: str = ""):
        headers = {"x-apikey": self.config.api_key, "accept": "application/json",
                   "User-Agent": "OpenGrab"}
        if ctype:
            headers["content-type"] = ctype
        status, raw = self.http(method, url, headers, body, 60.0)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        return status, data

    def _verdict(self, sha: str, stats: dict, results: Optional[dict] = None) -> VtResult:
        mal, sus = int(stats.get("malicious", 0)), int(stats.get("suspicious", 0))
        engines = sum(int(v) for k, v in stats.items() if k in (
            "malicious", "suspicious", "undetected", "harmless"))
        names = []
        for engine, r in (results or {}).items():
            if (r or {}).get("category") == "malicious" and r.get("result"):
                names.append(f"{engine}: {r['result']}")
        status = FLAGGED if mal >= self.config.min_detections else CLEAN
        detail = f"{mal} of {engines} engines flagged this file" + (f", {sus} found it suspicious" if sus else "")
        return VtResult(status, sha, mal, sus, engines, detail, names[:5])

    def lookup(self, sha: str) -> Optional[VtResult]:
        """The existing report, or None when VirusTotal has never seen the file."""
        status, data = self._call("GET", f"{API}/files/{sha}")
        if status == 404:
            return None
        if status == 401:
            raise PermissionError("VirusTotal rejected the API key")
        if status == 429:
            raise RuntimeError("VirusTotal's request limit was reached; try again in a minute")
        if status != 200:
            raise RuntimeError(f"VirusTotal answered HTTP {status}")
        attrs = (data.get("data") or {}).get("attributes") or {}
        return self._verdict(sha, attrs.get("last_analysis_stats") or {}, attrs.get("last_analysis_results"))

    def upload(self, path: str, sha: str) -> VtResult:
        size = Path(path).stat().st_size
        url = f"{API}/files"
        if size > UPLOAD_LIMIT:
            st, data = self._call("GET", f"{API}/files/upload_url")
            if st != 200 or not data.get("data"):
                raise RuntimeError(f"VirusTotal would not take a {size // 1048576} MB upload (HTTP {st})")
            url = str(data["data"])
        boundary = "opengrab" + uuid.uuid4().hex
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{Path(path).name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        body = head + Path(path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        st, data = self._call("POST", url, body, f"multipart/form-data; boundary={boundary}")
        if st not in (200, 201):
            raise RuntimeError(f"VirusTotal upload failed (HTTP {st})")
        analysis = (data.get("data") or {}).get("id", "")
        deadline = time.monotonic() + self.config.timeout_s
        while analysis and time.monotonic() < deadline:
            self.sleep(15)
            st, data = self._call("GET", f"{API}/analyses/{analysis}")
            attrs = (data.get("data") or {}).get("attributes") or {}
            if st == 200 and attrs.get("status") == "completed":
                return self._verdict(sha, attrs.get("stats") or {}, attrs.get("results"))
        return VtResult(QUEUED, sha, detail="uploaded; VirusTotal is still analysing it, open the report later")

    def check(self, path: str) -> VtResult:
        if not self.config.api_key:
            return VtResult(ERROR, detail="add your VirusTotal API key in Settings first")
        try:
            sha = sha256_of(path)
        except OSError as exc:
            return VtResult(ERROR, detail=f"cannot read the file: {exc}")
        try:
            found = self.lookup(sha)
            if found is not None:
                return found
            if not self.config.upload_unknown:
                return VtResult(UNKNOWN, sha, detail="VirusTotal has never seen this file (uploading is off)")
            return self.upload(path, sha)
        except Exception as exc:  # network, rate limit, bad key: never break the download
            return VtResult(ERROR, sha, detail=str(exc)[:300])
