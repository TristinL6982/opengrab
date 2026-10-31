"""FTP / FTPS downloads (ftp:// and ftps:// links).

Uses Python's ftplib, so no extra dependency:
  * login from the URL (ftp://user:password@host/path), anonymous otherwise
  * ftps:// = explicit TLS (AUTH TLS) on port 21; port 990 = implicit TLS
  * SIZE for the total, REST to resume an existing .part file
  * the same stop/pause events, progress ticks and token-bucket rate limiter
    as the HTTP downloader (LinkDownload hands itself to download())
  * directory URLs (ending in /) are listed with MLSD, falling back to LIST

The password is never logged or put into an error message.
"""
from __future__ import annotations

import ftplib
import logging
import os
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import quote, unquote, urlsplit, urlunsplit

log = logging.getLogger("opengrab.ftp")

READ_CHUNK = 64 * 1024
FTP_SCHEMES = ("ftp", "ftps")
ANON_USER = "anonymous"
ANON_PASS = "anonymous@"


def is_ftp_url(url: str) -> bool:
    return urlsplit(str(url or "")).scheme.lower() in FTP_SCHEMES


@dataclass
class FtpTarget:
    scheme: str
    host: str
    port: int
    user: str
    password: str
    path: str            # decoded server path, always starts with "/"

    @property
    def tls(self) -> bool:
        return self.scheme == "ftps"

    @property
    def implicit_tls(self) -> bool:
        return self.tls and self.port == 990

    @property
    def is_dir(self) -> bool:
        return self.path.endswith("/")

    @property
    def name(self) -> str:
        return PurePosixPath(self.path.rstrip("/")).name or self.host

    def safe_url(self) -> str:
        """The URL without the password (for logs and messages)."""
        netloc = self.host if self.port in (21, 990) else f"{self.host}:{self.port}"
        if self.user and self.user != ANON_USER:
            netloc = f"{quote(self.user, safe='')}@{netloc}"
        return f"{self.scheme}://{netloc}{quote(self.path)}"


def parse_ftp_url(url: str) -> FtpTarget:
    parts = urlsplit(str(url or ""))
    scheme = parts.scheme.lower()
    if scheme not in FTP_SCHEMES:
        raise ValueError("not an ftp:// or ftps:// link")
    host = parts.hostname or ""
    if not host:
        raise ValueError("the ftp link has no host")
    try:
        port = parts.port or 21
    except ValueError:
        raise ValueError("the ftp link has an invalid port")
    user = unquote(parts.username) if parts.username else ""
    password = unquote(parts.password) if parts.password is not None else ""
    if not user:
        user, password = ANON_USER, ANON_PASS
    # ";type=i" style suffixes (RFC 1738) are not part of the file name
    path = unquote(parts.path.split(";", 1)[0]) or "/"
    if not path.startswith("/"):
        path = "/" + path
    return FtpTarget(scheme, host, port, user, password, path)


def child_url(base_url: str, name: str, is_dir: bool = False) -> str:
    """URL of an entry in a directory listing (keeps the login of the base)."""
    parts = urlsplit(base_url)
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    # safe="": a "/" in a server-sent name must not turn into extra path levels
    path += quote(name, safe="") + ("/" if is_dir else "")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class _FTP_TLS(ftplib.FTP_TLS):
    """Explicit FTPS that reuses the control connection's TLS session for
    data connections. Many servers (vsftpd, FileZilla Server, Rebex) refuse
    a data connection without it ("425 TLS session resumption required")."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host,
                                            session=getattr(self.sock, "session", None))
        return conn, size


class _ImplicitFTP_TLS(_FTP_TLS):
    """FTPS on port 990: the TLS handshake comes before the greeting."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address
        sock = socket.create_connection((self.host, self.port), self.timeout,
                                        source_address=self.source_address)
        self.af = sock.family
        self.sock = self.context.wrap_socket(sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


def connect(target: FtpTarget, timeout: float = 30.0) -> ftplib.FTP:
    """Open, log in and switch to binary mode. Raises ftplib/OS errors."""
    if target.tls:
        ctx = ssl.create_default_context()
        cls = _ImplicitFTP_TLS if target.implicit_tls else _FTP_TLS
        ftp = cls(context=ctx, timeout=timeout)
    else:
        ftp = ftplib.FTP(timeout=timeout)
    ftp.encoding = "utf-8"
    try:
        ftp.connect(target.host, target.port)
        ftp.login(target.user, target.password)
        if target.tls:
            ftp.prot_p()          # encrypt the data connections too
        ftp.voidcmd("TYPE I")
    except BaseException:
        close_quietly(ftp)
        raise
    return ftp


def close_quietly(ftp: Optional[ftplib.FTP]) -> None:
    if ftp is None:
        return
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


def remote_size(ftp: ftplib.FTP, path: str) -> Optional[int]:
    try:
        n = ftp.size(path)
    except (ftplib.Error, OSError, EOFError):
        return None
    return int(n) if n is not None and int(n) >= 0 else None


def describe_error(e: BaseException) -> str:
    """A short message for the user (never contains the password)."""
    msg = str(e).strip() or type(e).__name__
    code = msg[:3]
    if code == "530":
        return "FTP login failed (check the user name and password in the link)"
    if code == "550":
        return "FTP: file or folder not found or not allowed"
    if isinstance(e, socket.timeout) or "timed out" in msg.lower():
        return "FTP: the server did not answer in time"
    return f"FTP error: {msg[:200]}"


def is_transient(e: BaseException) -> bool:
    if isinstance(e, ftplib.error_temp):           # 4xx: try again later
        return True
    if isinstance(e, ftplib.error_perm):           # 5xx: will not change
        return False
    return isinstance(e, (OSError, EOFError, ftplib.error_reply, ftplib.error_proto))


# ---------------------------------------------------------------------------
# directory listing
# ---------------------------------------------------------------------------
def _parse_list_line(line: str) -> Optional[tuple[str, bool, Optional[int]]]:
    """One line of a Unix-style LIST answer -> (name, is_dir, size)."""
    parts = line.split(None, 8)
    if len(parts) < 9 or parts[0][:1] not in "-dl":
        return None
    name = parts[8]
    if parts[0][0] == "l" and " -> " in name:
        name = name.split(" -> ", 1)[0]
    size = int(parts[4]) if parts[4].isdigit() else None
    return name, parts[0][0] == "d", size


def list_dir(ftp: ftplib.FTP, path: str) -> list[tuple[str, bool, Optional[int]]]:
    """Entries of a directory as (name, is_dir, size), without . and .."""
    out: list[tuple[str, bool, Optional[int]]] = []
    try:
        for name, facts in ftp.mlsd(path, facts=["type", "size"]):
            kind = (facts.get("type") or "").lower()
            if kind in ("cdir", "pdir") or name in (".", ".."):
                continue
            size = facts.get("size")
            out.append((name, kind == "dir",
                        int(size) if size and size.isdigit() else None))
        return out
    except ftplib.error_perm:
        pass                                      # no MLSD: plain LIST
    lines: list[str] = []
    ftp.retrlines("LIST " + path if path else "LIST", lines.append)
    for line in lines:
        entry = _parse_list_line(line)
        if entry and entry[0] not in (".", ".."):
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# the transfer (called by downloader.LinkDownload.run)
# ---------------------------------------------------------------------------
class _Paused(Exception):
    pass


def download(dl) -> "os.PathLike":
    """Download ``dl.url`` (an ftp/ftps URL) into ``dl.part`` and finalize.

    ``dl`` is the engine.downloader.LinkDownload; its stop/pause events,
    rate limiter, progress counters and _finalize() are reused so the
    manager sees an FTP download exactly like an HTTP one.
    """
    from .downloader import DownloadError, _cancelled

    try:
        target = parse_ftp_url(dl.url)
    except ValueError as e:
        raise DownloadError(str(e))
    if target.is_dir:
        raise DownloadError("this ftp link is a folder; add the files inside it")
    timeout = float(getattr(dl.settings, "timeout_s", 0) or 30.0)
    if dl._own_limiter:   # settings value is KB/s, as for HTTP
        limit = (getattr(dl.settings, "speed_limit_kbps", 0) or 0) * 1024
        dl._limiter.set_rate(limit)

    restart_tried = False
    while True:
        dl._stop_check()
        dl._sleep_paused()                         # cancel wins over pause
        ftp = None
        try:
            ftp = connect(target, timeout)
            total = remote_size(ftp, target.path)
            part_size = dl.part.stat().st_size if dl.part.exists() else 0
            if total is not None and part_size > total:
                part_size = 0
                dl.part.unlink()
            with dl._state_lock:
                dl._total = total
                dl._done = part_size
                dl.start_done = part_size
            if total is not None and part_size == total:
                break                              # already complete
            _retrieve(dl, ftp, target, part_size)
            break
        except _Paused:
            close_quietly(ftp)
            ftp = None
            continue                               # wait, then REST from .part
        except DownloadError:
            raise
        except ftplib.error_perm as e:
            # REST refused: the server cannot resume, start again from zero
            if (not restart_tried and str(e)[:3] in ("500", "501", "502", "504")
                    and dl.part.exists() and dl.part.stat().st_size > 0):
                restart_tried = True
                dl.part.unlink()
                continue
            if dl.stop_event.is_set():
                raise _cancelled()
            raise DownloadError(describe_error(e), transient=False)
        except Exception as e:
            if dl.stop_event.is_set():
                raise _cancelled()
            raise DownloadError(describe_error(e), transient=is_transient(e))
        finally:
            close_quietly(ftp)

    if (dl._total is not None and dl.part.exists()
            and dl.part.stat().st_size != dl._total):
        raise DownloadError("size mismatch", transient=True)
    if not dl.part.exists():
        dl.part.touch()                            # a genuinely empty file
    dl._finalize()
    dl._drop_meta()
    return dl.final_path


def _retrieve(dl, ftp: ftplib.FTP, target: FtpTarget, offset: int) -> None:
    from .downloader import _cancelled
    conn = ftp.transfercmd("RETR " + target.path, rest=offset or None)
    aborted = False
    try:
        with open(dl.part, "ab" if offset else "wb") as f:
            while True:
                if dl.stop_event.is_set():
                    aborted = True
                    raise _cancelled()
                if dl.pause_event.is_set():
                    aborted = True
                    raise _Paused()
                data = conn.recv(READ_CHUNK)
                if not data:
                    break
                dl._limiter.consume(len(data), dl.stop_event)
                f.write(data)
                dl._update_speed(len(data))
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not aborted:
        ftp.voidresp()                             # 226 transfer complete


def probe(url: str, timeout: float = 30.0) -> dict:
    """Metadata for the plugin: {"name", "size"} for a file or
    {"entries": [(url, name, is_dir, size)]} for a directory."""
    target = parse_ftp_url(url)
    ftp = connect(target, timeout)
    try:
        if target.is_dir:
            entries = list_dir(ftp, target.path)
            return {"entries": [(child_url(url, n, d), n, d, s) for n, d, s in entries]}
        try:
            size = ftp.size(target.path)
        except ftplib.error_perm as e:
            if str(e)[:3] == "550":          # no such file (or not allowed)
                raise
            size = None                      # SIZE not supported: unknown size
        return {"name": target.name, "size": int(size) if size is not None else None}
    finally:
        close_quietly(ftp)
