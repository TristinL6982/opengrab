"""DownloadManager — the engine's brain (JD2's "grabber" + "queue" hybrid).

Responsibilities
  * grab:     url -> plugin selection -> grab()/resolve() -> Link
              (CaptchaRequired / LoginRequired / PaywallRequired park the
              link in a resumable state until the user supplies input)
  * queue:    prioritized waiting list (priority desc, then created asc)
  * download: N worker threads (settings.max_parallel)
  * packages: parent links with child links the user can queue
  * persist:  link state mirrored to %APPDATA%/OpenGrab/links.json
  * events:   on_change(kind, payload) -> API broadcast -> UI live update

State model (deliberately JD2-shaped):  there is no separate RESOLVED
state — a link that has been resolved simply sits in WAITING with its
direct_url set, until a worker picks it up.

All public methods are thread-safe.  start() spawns workers; shutdown()
pauses active downloads and saves state.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from ..accounts import AccountManager
from ..captcha.chain import SolverChain
from ..filters import FilterManager
from ..http_client import HttpClient
from ..models import Link, LinkStatus
from ..plugins.base import (CaptchaRequired, LoginRequired, PaywallRequired,
                            PluginContext, PluginError)
from ..plugins.registry import PluginRegistry
from ..settings import Settings, app_data_dir, user_plugins_dir
from .downloader import DownloadError, LinkDownload, _RateLimiter
from ..sessiondb import SessionDB

log = logging.getLogger("opengrab.engine")

_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _existing_unique(paths) -> list:
    """The paths that exist, each once (case-insensitive on Windows)."""
    seen: set = set()
    out = []
    for c in paths:
        key = os.path.normcase(str(c))
        if key not in seen and os.path.lexists(c):
            seen.add(key)
            out.append(c)
    return out


def _local_torrent_path(url: str) -> Optional[Path]:
    """The filesystem path of a local .torrent given as file:// or a Windows path.

    None for anything else.  A file:// URL that is not a .torrent raises ValueError:
    local files are never read, fetched or forwarded except as torrents."""
    from urllib.request import url2pathname
    if url[:5].lower() == "file:":
        parts = urlparse(url)
        if not parts.path.lower().endswith(".torrent"):
            raise ValueError("only local .torrent files can be added from a file:// link")
        path = parts.path
        if parts.netloc and parts.netloc.lower() != "localhost":
            path = "//" + parts.netloc + path  # file://server/share/x.torrent (UNC)
        elif re.match(r"^/[A-Za-z]:", path):
            path = path[1:]  # /C:/x -> C:/x on any OS
        return Path(unquote(path) if _WIN_PATH.match(path) else url2pathname(path))
    if _WIN_PATH.match(url) and url.lower().endswith(".torrent"):
        return Path(url)
    return None


class DownloadManager:
    def __init__(self, settings: Settings, on_change=None):
        self.settings = settings
        self.data_dir = app_data_dir()
        self.links_file = self.data_dir / "links.json"
        self.session_file = self.data_dir / "session.db"
        self.on_change = on_change or (lambda kind, payload: None)

        self._links: dict[str, Link] = {}
        self._lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self._ready: "queue.PriorityQueue[tuple[int, float, str]]" = queue.PriorityQueue()
        self._active: dict[str, LinkDownload] = {}
        self._reserved_downloads: set[tuple[str, str]] = set()
        self._global_limiter = _RateLimiter(
            max(0, settings.speed_limit_kbps) * 1024)

        self.client = HttpClient(settings)
        self.accounts = AccountManager()
        self.filters = FilterManager()
        self.registry = PluginRegistry()
        # W2: Cloudflare clearance cookies and the shared captcha inbox
        from ..captcha.inbox import CaptchaInbox
        from ..clearance import ClearanceStore
        self.clearance = ClearanceStore(str(self.data_dir / "clearance.json"))
        self.client.clearance = self.clearance
        self.captcha_inbox = CaptchaInbox(
            max_pending=max(1, int(getattr(settings, "captcha_inbox_max_pending", 50) or 50)))
        self.captcha_chain = SolverChain(settings, self.client, clearance_store=self.clearance)
        # many links on one host share one browser window: each widget captcha gets
        # its own token from one page (see captcha/batcher.py); one Edge at a time
        from ..captcha.batcher import WidgetBatcher
        self.captcha_batcher = None
        browser = next((x for x in self.captcha_chain.solvers if x.id == "browser"), None)
        if browser is not None:
            self.captcha_batcher = WidgetBatcher(browser.solve_batch)
            browser.batcher = self.captcha_batcher
        # W3-W5: scheduler, host limits, queues, speed profiles, data cap
        from .flow import FlowControl
        from .net import NetPolicy
        self.flow = FlowControl(settings, self.data_dir)
        self.net = NetPolicy(settings)  # W7: proxies and server/proxy logins
        self.client.net = self.net
        # W9: account choice (premium first, quotas), cookies, debrid
        from .accounts_flow import AccountPolicy, AccountsView
        self.account_policy = AccountPolicy(self.accounts, self.client, self.data_dir, settings)
        self.accounts_view = AccountsView(self.accounts, self.account_policy)
        from .postprocess import VirusScan
        self.virus_scan = VirusScan(settings)  # W10
        from .torrents import TorrentService
        self.torrents = TorrentService(self)  # W11 (libtorrent add-on)
        from .plugin_policy import PluginPolicy
        self.plugin_policy = PluginPolicy(self)  # W12
        self._deferred: set[str] = set()
        self._tick_done: dict[str, int] = {}
        self._schedule_paused = False
        try:
            self.accounts.load()
            self.filters.load()
        except Exception:
            log.warning("loading accounts/filters", exc_info=True)

        self._workers: list[threading.Thread] = []
        self._started = False
        self._retries: dict[str, int] = {}
        self._last_persist = 0.0
        self._persist_scheduled = False
        # Initialize to None so we can check it later
        self._db = None

    def _session(self):
        """Lazily open and cache SessionDB."""
        if self._db is None:
            self._db = SessionDB(str(self.session_file))
        return self._db

    # ------------------------------------------------------------------ init
    def start(self) -> None:
        if self._started:
            return
        self._load_links()
        try:
            self.registry.set_user_dir(user_plugins_dir())
            self.registry.scan()
        except Exception:
            log.warning("plugin scan failed", exc_info=True)
        self._spawn(self.plugin_policy.maybe_auto_update)  # W12
        n = self.settings.max_parallel or 3
        MAX_WORKERS = 16
        for i in range(MAX_WORKERS):
            t = threading.Thread(target=self._worker, args=(i,), name=f"worker-{i}",
                                 daemon=True)
            t.start()
            self._workers.append(t)
        self._started = True
        threading.Thread(target=self._flow_loop, name="flow-control", daemon=True).start()
        for l in self.all_links():  # W11: torrents survive a restart
            t = (l.extra or {}).get("torrent") or {}
            if self._is_torrent(l) and l.status not in (LinkStatus.FINISHED, LinkStatus.SKIPPED) \
                    and (t.get("magnet") or t.get("torrent_file")):
                resume_paused = l.status == LinkStatus.PAUSED
                self._spawn(self._restore_torrent, l.id, t.get("magnet", ""), t.get("torrent_file", ""),
                            resume_paused)
        log.info("engine started (%d workers)", n)

    def shutdown(self) -> None:
        with self._lock:
            for l in self._links.values():
                if l.status == LinkStatus.DOWNLOADING:
                    l.status = LinkStatus.PAUSED
                    l.extra["note"] = "was downloading when app closed"
        for dl in list(self._active.values()):
            try:
                dl.stop_event.set()
            except Exception:
                pass
        try:
            self.torrents.shutdown()
        except Exception:
            log.debug("torrent shutdown failed", exc_info=True)
        # Give transfers up to 3 s to checkpoint their chunk maps and exit.
        deadline = time.monotonic() + 3.0
        while self._active and time.monotonic() < deadline:
            time.sleep(0.1)
        self._save_now()
        if self._db is not None:
            self._db.close()
            self._db = None
        self._started = False
        log.info("engine stopped")

    # ----------------------------------------------------------------- links
    def all_links(self) -> list[Link]:
        with self._lock:
            return sorted(self._links.values(), key=lambda l: l.created)

    def get(self, link_id: str) -> Optional[Link]:
        with self._lock:
            return self._links.get(link_id)

    def _broadcast(self, kind: str, link: Optional[Link] = None) -> None:
        try:
            self.on_change(kind, link.to_public() if link else self.stats())
        except Exception:
            log.debug("on_change failed", exc_info=True)

    def stats(self) -> dict:
        with self._lock:
            links = list(self._links.values())
        by = lambda s: sum(1 for l in links if l.status == s)
        try:
            active_bps = sum(d.speed_bps() for d in self._active.values())
        except Exception:
            active_bps = 0.0
        return {
            "counts": {s.value: by(s) for s in LinkStatus},
            "total": len(links),
            "speed_bps": active_bps,
            "downloaded": sum(l.downloaded for l in links
                              if l.status == LinkStatus.FINISHED),
        }

    # ------------------------------------------------------------------ grab
    def add_url(self, url: str, priority: int = 0,
                auto_download: bool = True, title: str = "",
                filename: str = "", queue: str = "", expected_hash: str = "",
                save_dir: str = "") -> Link:
        """Register a URL and run the grab phase (async).  Returns the Link
        immediately (status RESOLVING, or SKIPPED if a filter excludes it)."""
        url = (url or "").strip()
        if not url:
            raise ValueError("empty url")
        local = _local_torrent_path(url)  # file:///x.torrent or C:\x.torrent; other file:// raises
        if local is not None:
            return self._add_local_torrent(url, local, priority, title, queue, save_dir)
        extra: dict = {}
        if save_dir:  # a folder chosen for this link; categories/packagizer subfolders go inside it
            from ..savedir import validate_save_dir
            extra["save_dir"] = str(validate_save_dir(save_dir, create=False))
        if queue:
            extra["queue"] = self.flow.queues.get(queue).name  # KeyError: unknown queue
        if expected_hash:
            from ..hashverify import parse_expected
            algo, digest = parse_expected(expected_hash)  # ValueError: bad checksum
            extra["expected_hash"] = f"{algo}:{digest}"
        allowed, reason = self.filters.allows(url, filename)
        if not allowed:
            link = Link(url=url, title=title, filename=filename,
                        status=LinkStatus.SKIPPED,
                        extra={"reason": reason or "blocked by filter"})
            with self._lock:
                self._links[link.id] = link
            self._persist()
            self._broadcast("grabbed", link)
            return link

        link = Link(url=url, title=title, filename=filename,
                    priority=priority, status=LinkStatus.RESOLVING, extra=extra)
        with self._lock:
            self._links[link.id] = link
        self._persist()
        self._broadcast("grabbed", link)
        if url.lower().startswith("magnet:?"):
            link.plugin = "torrent"
            self._spawn(self._start_torrent, link.id, url, "")
            return link

        self._spawn(self._grab_and_queue, link.id, auto_download)
        return link

    # ------------------------------------------------------------ W11 torrents
    def _start_torrent(self, link_id: str, magnet: str, torrent_file: str) -> None:
        link = self.get(link_id)
        if link is None:
            return
        try:
            self.torrents.add_link(link, magnet=magnet, torrent_file=torrent_file)
        except Exception as exc:
            link.status = LinkStatus.ERROR
            link.error = f"could not start the torrent: {exc}"
        self._persist()
        self._broadcast("error" if link.status == LinkStatus.ERROR else "grabbed", link)

    def _restore_torrent(self, link_id, magnet, torrent_file, paused) -> None:
        self._start_torrent(link_id, magnet, torrent_file)
        link = self.get(link_id)
        if paused and link is not None and link.status != LinkStatus.ERROR:
            self.torrents.pause(link_id)
            link.status = LinkStatus.PAUSED

    def link_root(self, link: Link) -> Path:
        """The folder this link downloads into: its own "Save to" folder, else the default."""
        return Path(str(link.extra.get("save_dir") or self.settings.download_dir)).expanduser().resolve()

    def _add_local_torrent(self, url: str, path: Path, priority: int, title: str, queue: str,
                           save_dir: str = "") -> Link:
        """A file:// (or plain path) .torrent: validate it, keep a private copy in the
        app data dir (so the download survives the original being moved or deleted)
        and load it as a torrent file, never as a URL."""
        import hashlib
        try:
            if not path.is_file():
                raise ValueError(f"torrent file not found: {path}")
            if path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError(f"not a valid torrent file (too large): {path.name}")
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read the torrent file {path.name}: {exc.strerror or exc}") from exc
        return self._add_torrent_data(data, path.name, url, priority, title or path.stem, queue, save_dir)

    def add_torrent_bytes(self, data: bytes, name: str, title: str = "", priority: int = 0, queue: str = "",
                          save_dir: str = "") -> Link:
        """Torrent bytes that arrived without a path (dropped onto the window): same
        validation and private copy as a local file."""
        name = Path(str(name or "").replace("\\", "/")).name or "dropped.torrent"
        if len(data) > 32 * 1024 * 1024:
            raise ValueError(f"not a valid torrent file (too large): {name}")
        return self._add_torrent_data(data, name, "", priority, title or Path(name).stem, queue, save_dir)

    def _add_torrent_data(self, data: bytes, name: str, url: str, priority: int, title: str, queue: str,
                          save_dir: str = "") -> Link:
        import hashlib
        if not data.startswith(b"d"):
            raise ValueError(f"not a valid torrent file: {name}")
        from .torrents import libtorrent_available
        if libtorrent_available():
            import libtorrent as lt
            try:
                lt.torrent_info(lt.bdecode(data))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"not a valid torrent file: {name} ({exc})") from exc
        extra: dict = {}
        if queue:
            extra["queue"] = self.flow.queues.get(queue).name
        if save_dir:
            from ..savedir import validate_save_dir
            extra["save_dir"] = str(validate_save_dir(save_dir, create=False))
        copy_dir = self.data_dir / "torrents"
        copy_dir.mkdir(parents=True, exist_ok=True)
        copy = copy_dir / (hashlib.sha1(data).hexdigest() + ".torrent")
        if not copy.exists():
            copy.write_bytes(data)
        # recorded up front so a retry or a restart finds the file even if the first start fails
        extra["torrent"] = {"info_hash": "", "magnet": "", "torrent_file": str(copy), "save_path": ""}
        link = Link(url=url or copy.as_uri(), title=title, priority=priority,
                    status=LinkStatus.RESOLVING, plugin="torrent", extra=extra)
        with self._lock:
            self._links[link.id] = link
        self._persist()
        self._broadcast("grabbed", link)
        self._spawn(self._start_torrent, link.id, "", str(copy))
        return link

    def add_torrent_file(self, path: str, title: str = "") -> Link:
        link = Link(url=Path(path).as_uri(), title=title or Path(path).stem,
                    status=LinkStatus.RESOLVING, plugin="torrent")
        with self._lock:
            self._links[link.id] = link
        self._persist()
        self._broadcast("grabbed", link)
        self._spawn(self._start_torrent, link.id, "", str(path))
        return link

    def _is_torrent(self, link: Optional[Link]) -> bool:
        return link is not None and link.plugin == "torrent"

    def _spawn(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True,
                         name="engine-task").start()

    def _ctx(self) -> PluginContext:
        return PluginContext(client=self.client, settings=self.settings,
                             accounts=self.accounts_view,
                             captcha_chain=self.captcha_chain,
                             logger=log)

    def _ctx_for(self, plugin) -> PluginContext:
        """W12: user plugins and templates get a sandboxed context."""
        return self.plugin_policy.context_for(plugin, self._ctx())

    def _pick_plugin(self, link: Link):
        plugins = self.registry.for_url(link.url)
        if not plugins:
            raise PluginError(f"no plugin supports this url: {link.url}")
        return plugins[0]

    def _refresh_expired_link(self, link: Link, msg: str) -> bool:
        """Hosts like Mega and GoFile hand out download links that expire. When
        one answers 401/403/404/410 (often after a restart or a long pause),
        ask the plugin once for a fresh link instead of failing."""
        import re as _re
        if not _re.match(r"HTTP (401|403|404|410)\b", msg) or link.plugin in ("", "direct", "generic"):
            return False
        if link.extra.get("link_refreshed"):
            link.extra.pop("link_refreshed", None)  # refreshed once already: a real failure
            return False
        plugin = self.registry.get(link.plugin)
        if plugin is None:
            return False
        link.extra["link_refreshed"] = True
        link.direct_url, link.direct_headers = None, {}
        link.status, link.error = LinkStatus.RESOLVING, ""
        self._broadcast("grabbed", link)

        def refresh():
            try:
                self._resolve(link, plugin, self._ctx_for(plugin))
                if link.status == LinkStatus.WAITING and link.direct_url:
                    self._ready_put(link.id)
                    self._broadcast("queued", link)
                    return
                raise PluginError("the host gave no new link")
            except CaptchaRequired as e:
                self._handle_challenge(link, e, phase="download")
            except LoginRequired as e:
                link.status, link.login_hint = LinkStatus.LOGIN, e.hint
            except PaywallRequired as e:
                link.status, link.paywall_note = LinkStatus.PAYWALL, e.note
            except Exception as e:  # noqa: BLE001
                link.extra.pop("link_refreshed", None)
                self._fail(link, f"{msg}; asking the host for a new link failed: {e}", False)
            self._persist()
            self._broadcast("grabbed", link)

        self._spawn(refresh)
        return True

    def _plugin_finalize(self, link: Link, path: Path) -> Path:
        """Let the plugin finish the file after the transfer, e.g. decrypt a
        Mega download (hosts that send files encrypted). The plugin returns
        the final path; an error fails the download with its message."""
        plugin = self.registry.get(link.plugin) if link.plugin else None
        fin = getattr(plugin, "finalize", None)
        if fin is None:
            return path
        out = fin(path, link, self._ctx_for(plugin))
        return Path(out) if out else path

    def _grab_with_fallback(self, link: Link) -> None:
        """Try the plugins that claim the link, most specific first. When one
        fails outright (an extractor refusing the site, a page it cannot
        read), the next one gets the link, ending with the generic page
        grabber. Captchas, logins and paywalls stop the search: they are
        answers, not failures."""
        plugins = self.registry.for_url(link.url)
        if not plugins:
            raise PluginError(f"no plugin supports this url: {link.url}")
        first_error: Optional[Exception] = None
        for plugin in plugins:
            link.plugin = plugin.id
            ctx = self._ctx_for(plugin)
            try:
                try:
                    g = plugin.grab(link.url, ctx)
                except (CaptchaRequired, LoginRequired, PaywallRequired):
                    raise
                except (NotImplementedError, PluginError):
                    g = None  # metadata is optional: resolve may still work
                if g is not None:
                    self._apply_grab(link, plugin.id, g)
                if link.status not in (LinkStatus.CAPTCHA, LinkStatus.LOGIN,
                                       LinkStatus.PAYWALL, LinkStatus.SKIPPED):
                    self._resolve(link, plugin, ctx)
                if link.status != LinkStatus.ERROR:
                    return
                failure: Exception = PluginError(link.error or "failed")
            except (CaptchaRequired, LoginRequired, PaywallRequired):
                raise
            except PluginError as e:
                failure = e
            log.info("%s could not handle %s (%s); trying the next plugin", plugin.id, link.url, failure)
            first_error = first_error or failure
            link.status, link.error = LinkStatus.RESOLVING, ""
            link.direct_url, link.direct_headers = None, {}
        raise first_error or PluginError("no plugin could handle this link")

    def _plugin_for(self, link: Link):
        """Registered plugin for the link, falling back to url matching."""
        p = self.registry.get(link.plugin) if link.plugin else None
        if p is not None:
            return p
        return self._pick_plugin(link)

    def _grab_and_queue(self, link_id: str, auto_download: bool) -> None:
        link = self.get(link_id)
        if link is None:
            return
        try:
            self._grab_with_fallback(link)
        except CaptchaRequired as e:
            if self._handle_challenge(link, e, phase="grab"):
                return
        except LoginRequired as e:
            link.status = LinkStatus.LOGIN
            link.login_hint = e.hint
        except PaywallRequired as e:
            link.status = LinkStatus.PAYWALL
            link.paywall_note = e.note
        except Exception as e:
            link.status = LinkStatus.ERROR
            link.error = str(e)
            log.warning("grab failed for %s: %s", link.url, e, exc_info=True)
        self._persist()
        self._broadcast("grabbed", link)

        if (auto_download
                and link.status == LinkStatus.WAITING
                and link.direct_url):
            self._ready_put(link.id)
            self._broadcast("queued", link)

    # ----------------------------------------------- W2 challenges / inbox
    def _park_captcha(self, link: Link, e, phase: str) -> None:
        link.status = LinkStatus.CAPTCHA
        link.captcha_image = e.image_b64
        link.captcha_kind = e.kind
        extra = e.extra or {}
        link.extra.update({
            "captcha_site_url": e.site_url,
            "captcha_sitekey": e.sitekey,
            "captcha_image_url": extra.get("image_url", ""),
            "captcha_phase": phase,
        })
        for key in ("challenge", "ray_id", "solver_error"):
            if extra.get(key):
                link.extra["captcha_" + key] = extra[key]
        from ..challenge import INBOX_KINDS
        if e.kind in INBOX_KINDS:
            host = urlparse(e.site_url or link.direct_url or link.url).hostname or ""
            try:
                req = self.captcha_inbox.submit(
                    host, e.kind, e.site_url or link.url, sitekey=e.sitekey or None,
                    timeout_s=max(60, int(self.settings.captcha_manual_timeout_s or 120) * 5))
                link.extra["captcha_request_id"] = req.id
            except Exception as exc:  # inbox full: the link stays parked
                link.extra["captcha_error"] = str(exc)

    def _auto_solve_challenge(self, link: Link, e) -> str:
        """Try the automatic solvers once per park. Returns "__CLEARANCE__",
        a widget token, or "" when a human is needed."""
        from ..captcha.base import CaptchaRequest
        from ..challenge import INBOX_KINDS
        if e.kind not in INBOX_KINDS or link.extra.get("challenge_auto_tried"):
            return ""
        link.extra["challenge_auto_tried"] = True
        from ..challenge import BROWSER_KINDS
        if e.kind in BROWSER_KINDS:
            # only a real browser passes a Cloudflare/DDoS-Guard interstitial
            browser = next((s for s in self.captcha_chain.solvers if s.id == "browser"), None)
            if browser is None or not browser.enabled():
                return ""
            req = CaptchaRequest(kind="turnstile", site_url=e.site_url or link.url)
            try:
                res = browser.solve(req, timeout_s=float(self.settings.captcha_manual_timeout_s or 120))
            except Exception as exc:
                log.info("browser challenge solve failed: %s", exc)
                return ""
            return res.text if res.ok and res.text == "__CLEARANCE__" else ""
        req = CaptchaRequest(kind=e.kind, site_url=e.site_url or link.url,
                             recaptcha_sitekey=e.sitekey or "")
        # with a sitekey the real-browser solver (opt-in) shows the widget on the
        # site's own page and returns its token, so it may run on its own too
        auto = [s.id for s in self.captcha_chain._ordered(e.kind)
                if s.id != "manual" and (s.id != "browser" or (e.sitekey and s.enabled()))]
        if not auto:
            return ""
        try:
            res = self.captcha_chain.solve(req, allowed=auto)
        except Exception as exc:
            log.info("automatic challenge solve failed: %s", exc)
            return ""
        if not res.ok or res.text in ("", "__MANUAL__"):
            return ""
        return res.text

    def _handle_challenge(self, link: Link, e, phase: str) -> bool:
        """Solve automatically or park. True when a retry was started."""
        answer = self._auto_solve_challenge(link, e)
        if answer == "__CLEARANCE__":
            link.extra["note"] = "challenge cleared in the browser"
            if phase == "download":
                link.status = LinkStatus.WAITING
                self._persist()
                self._ready_put(link.id)
                self._broadcast("queued", link)
            else:
                self._regrab(link)
            return True
        self._park_captcha(link, e, phase)
        if answer:
            self._persist()
            self.submit_captcha(link.id, answer)
            return True
        return False

    def _regrab(self, link: Link) -> None:
        link.status = LinkStatus.RESOLVING
        link.error = ""
        self._persist()
        self._broadcast("grabbing", link)
        self._spawn(self._grab_and_queue, link.id, True)

    def links_for_captcha(self, request_id: str) -> list[Link]:
        return [l for l in self.all_links()
                if l.status == LinkStatus.CAPTCHA
                and l.extra.get("captcha_request_id") == request_id]

    def captcha_request_solved(self, request_id: str, answer: str) -> int:
        """Resume every link waiting on an inbox request."""
        links = self.links_for_captcha(request_id)
        for link in links:
            link.extra.pop("challenge_auto_tried", None)
            if link.extra.get("captcha_phase") == "download" and answer == "__CLEARANCE__":
                link.status = LinkStatus.WAITING
                self._persist()
                self._ready_put(link.id)
                self._broadcast("queued", link)
            elif answer == "__CLEARANCE__":
                self._regrab(link)
            else:
                self.submit_captcha(link.id, answer)
        return len(links)

    def widget_captcha_groups(self) -> dict:
        """Pending widget captchas by (host, kind, sitekey): the links waiting
        (each needs its own token) and the inbox requests that cover them."""
        from ..challenge import WIDGET_KINDS
        groups: dict = {}
        for req in self.captcha_inbox.pending():
            if req.kind not in WIDGET_KINDS or not req.sitekey:
                continue
            links = self.links_for_captcha(req.id)
            if not links:
                continue
            g = groups.setdefault((req.host, req.kind, req.sitekey),
                                  {"page_url": req.page_url, "links": [], "requests": []})
            g["requests"].append(req.id)
            g["links"].extend(l for l in links if l not in g["links"])
        return groups

    def give_captcha_token(self, link: Link, token: str) -> None:
        """Hand one widget token to one parked link (it retries with exactly this token)."""
        link.extra.pop("challenge_auto_tried", None)
        self.submit_captcha(link.id, token)

    def captcha_request_skipped(self, request_id: str) -> int:
        links = self.links_for_captcha(request_id)
        for link in links:
            link.status = LinkStatus.ERROR
            link.error = "captcha skipped"
            self._persist()
            self._broadcast("error", link)
        return len(links)

    def retry_captcha(self, link_id: str) -> bool:
        """Grab or download a parked captcha link again (e.g. after expiry)."""
        link = self.get(link_id)
        if link is None or link.status != LinkStatus.CAPTCHA:
            return False
        link.extra.pop("challenge_auto_tried", None)
        if link.extra.get("captcha_phase") == "download":
            link.status = LinkStatus.WAITING
            self._persist()
            self._ready_put(link.id)
            self._broadcast("queued", link)
        else:
            self._regrab(link)
        return True

    # ------------------------------------------------------- W3 hash check
    def _expected_hash(self, link: Link, path: Path) -> str:
        expected = str(link.extra.get("expected_hash") or "").strip()
        if expected or not getattr(self.settings, "hash_checksum_files", False):
            return expected
        from ..hashverify import parse_checksum_file
        base = (link.direct_url or link.url).split("?", 1)[0]
        name = path.name
        for suffix, algo in ((".sha256", "sha256"), (".md5", "md5"), (".sha1", "sha1")):
            try:
                r = self.client.get(base + suffix, timeout=15, max_bytes=65536, detect_challenges=False)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            table = parse_checksum_file(r.text)
            hit = table.get(name) or (next(iter(table.values())) if len(table) == 1 else None)
            if hit:
                link.extra["hash_source"] = base + suffix
                return f"{hit[0]}:{hit[1]}"
        return ""

    def _verify_hash(self, link: Link, path: Path) -> None:
        """W3: compare the finished file with a known checksum."""
        expected = self._expected_hash(link, path)
        if not expected:
            return
        from ..hashverify import verify_file
        try:
            res = verify_file(path, expected)
        except ValueError as exc:
            link.extra["hash"] = {"ok": False, "error": f"bad checksum: {exc}"}
            return
        link.extra["hash"] = {"ok": res.ok, "algo": res.algo, "expected": res.expected,
                              "actual": res.actual}
        if not res.ok:
            # a corrupt file must not look finished; retry downloads it again
            try:
                path.unlink()
            except OSError:
                pass
            link.extra.pop("download_target_filename", None)
            link.downloaded = 0
            link.progress = 0.0
            raise DownloadError(f"hash mismatch: expected {res.algo} {res.expected[:16]}…, "
                                f"got {res.actual[:16]}…")

    def verify_now(self, link_id: str, expected: str = "") -> dict:
        link = self.get(link_id)
        if link is None or link.status != LinkStatus.FINISHED or not link.path:
            raise ValueError("only finished downloads can be checked")
        from ..hashverify import verify_file
        expected = (expected or link.extra.get("expected_hash") or "").strip()
        if not expected:
            raise ValueError("enter a checksum, e.g. sha256:<hex>")
        res = verify_file(Path(link.path), expected)
        link.extra["expected_hash"] = expected
        link.extra["hash"] = {"ok": res.ok, "algo": res.algo, "expected": res.expected,
                              "actual": res.actual}
        self._persist()
        self._broadcast("grabbed", link)
        return link.extra["hash"]

    # ------------------------------------------------ W10 post-processing
    def _extract(self, link: Link, path: Path) -> None:
        from .postprocess import extract_ready, set_members
        try:
            result = extract_ready(self, link, path)
        except Exception as exc:  # preserve the completed download
            result = {"status": "failed", "message": str(exc)[:500]}
        if result is None:
            return
        link.extra["archive_extraction"] = result
        members = set_members(path)
        if members is not None and result.get("status") != "waiting":
            # one extraction covers every part: tell the sibling links
            names = {n.lower() for n in members[1]}
            for other in self.all_links():
                if other.id != link.id and (other.filename or "").lower() in names:
                    other.extra["archive_extraction"] = dict(result, part_of_set=True)
                    self._broadcast("grabbed", other)

    def _scan_link(self, link_id: str) -> None:
        link = self.get(link_id)
        if link is None or not link.path:
            return
        flagged = False
        if self.virus_scan.enabled():
            res = self.virus_scan.scan(link.path)
            link.extra["avscan"] = {"status": res.status, "detail": res.detail[-300:], "code": res.code}
            flagged = res.flagged
        if self.virus_scan.vt_enabled() and not flagged:
            vt = self.virus_scan.vt_check(link.path)
            link.extra["virustotal"] = vt.to_dict()
            flagged = vt.flagged
        if flagged:
            cfg = self.virus_scan.config()
            if cfg.quarantine:
                try:
                    root = self.link_root(link)
                    moved = self.virus_scan.quarantine(Path(link.path), root)
                    link.path = str(moved)
                    link.extra.setdefault("avscan", {"status": "infected"})["quarantined"] = True
                except OSError as exc:
                    link.extra.setdefault("avscan", {"status": "infected"})["quarantine_error"] = str(exc)
            self.on_change("virus", link.to_public())
        self._persist()
        self._broadcast("grabbed", link)

    def _account_cookies(self, url: str) -> dict:
        """W9: cookies imported into the account for this host."""
        header = self.account_policy.cookie_header(url)
        out = {}
        for part in header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _clearance_extras(self, url: str) -> tuple[dict, dict]:
        host = urlparse(url).hostname or ""
        c = self.clearance.get(host) if host else None
        if c is None:
            return {}, {}
        return {"User-Agent": c.user_agent}, dict(c.cookies)

    def _apply_grab(self, link: Link, plugin_id: str, g) -> None:
        link.plugin = plugin_id
        if g.extra:
            link.extra.update(g.extra)
        if g.title:
            link.title = g.title
        if g.filename:
            link.filename = g.filename
        if g.size:
            link.size = g.size
        if g.site:
            link.site = g.site
        if g.direct_url:
            link.direct_url = g.direct_url
            link.direct_headers = dict(g.direct_headers or {})
            link.status = LinkStatus.WAITING
            return
        if g.children:
            link.children = [[str(u), str(t)] for u, t in g.children]
        if g.needs_captcha:
            link.status = LinkStatus.CAPTCHA
            link.captcha_kind = (g.extra or {}).get("captcha_kind", "text")
        elif g.needs_login:
            link.status = LinkStatus.LOGIN
        elif g.paywalled:
            link.status = LinkStatus.PAYWALL
        # else: stay RESOLVING — _resolve() produces the direct url

    # --------------------------------------------------------------- packages
    def _queue_children(self, parent: Link, auto: bool = True) -> list[Link]:
        out = []
        for item in list(parent.children or []):
            try:
                u = str(item[0])
                t = str(item[1]) if len(item) > 1 else ""
            except (TypeError, IndexError):
                continue
            child = self.add_url(u, priority=parent.priority,
                                 auto_download=auto, title=t,
                                 queue=str(parent.extra.get("queue") or ""))
            if child.status != LinkStatus.SKIPPED:
                child.parent = parent.id
            out.append(child)
        self._persist()
        return out

    def queue_child(self, parent_id: str, index: int) -> Optional[Link]:
        """Queue a single child of a package link (JD2 'queue child')."""
        parent = self.get(parent_id)
        if not parent or not parent.children:
            return None
        if not (0 <= index < len(parent.children)):
            return None
        item = parent.children[index]
        u = str(item[0])
        t = str(item[1]) if len(item) > 1 else ""
        for c in self.all_links():
            if c.parent == parent_id and c.url == u:
                return c
        child = self.add_url(u, priority=parent.priority,
                             auto_download=True, title=t)
        if child.status != LinkStatus.SKIPPED:
            child.parent = parent_id
            self._persist()
        return child

    def queue_all_children(self, parent_id: str) -> list[Link]:
        parent = self.get(parent_id)
        if not parent or not parent.children:
            return []
        return self._queue_children(parent, auto=True)

    def _resolve(self, link: Link, plugin, ctx: PluginContext) -> None:
        if link.direct_url:
            # Already resolved: the plugin is not called and no health is recorded.
            link.status = LinkStatus.WAITING
            return
        if link.status in (LinkStatus.CAPTCHA, LinkStatus.LOGIN,
                           LinkStatus.PAYWALL, LinkStatus.SKIPPED):
            return
        pid = getattr(plugin, "id", "?")
        if pid != "direct" and self._debrid(link):
            return  # W9: a debrid account unrestricted the hoster link
        try:
            r = plugin.resolve(link.url, ctx)
        except (CaptchaRequired, LoginRequired, PaywallRequired):
            # User-action states are not plugin failures: record nothing.
            raise
        except Exception as e:
            self.registry.record_result(pid, False, str(e))
            raise
        if r.extra:
            link.extra.update(r.extra)  # e.g. a decryption key for finalize()
        if r.url:
            link.direct_url = r.url
            link.direct_headers = dict(r.headers or {})
            if r.filename:
                link.filename = r.filename
            if r.size:
                link.size = r.size
            if r.children and not link.children:
                link.children = [[str(u), str(t)] for u, t in r.children]
            link.status = LinkStatus.WAITING
            self.registry.record_result(pid, True)
        elif r.children:
            # package parent: the children carry the download
            link.status = LinkStatus.WAITING
            self.registry.record_result(pid, True)
        else:
            self.registry.record_result(pid, False, "no direct url and no children")
            raise PluginError(
                f"plugin '{pid}' produced no direct url and no children")

    def _debrid(self, link: Link) -> bool:
        res = self.account_policy.debrid_resolve(link.url)
        if res is None or not res.url:
            return False
        link.direct_url = res.url
        link.direct_headers = dict(res.headers or {})
        if res.filename and not link.filename:
            link.filename = res.filename
        if res.size:
            link.size = res.size
        link.extra.update(res.extra or {})  # account id and service name
        link.status = LinkStatus.WAITING
        return True

    # ------------------------------------------------------------- download
    def _ready_put(self, link_id: str) -> None:
        link = self.get(link_id)
        if link is None:
            return
        # PriorityQueue sorts lower keys first; negate user priority and keep
        # insertion order stable by the link's creation timestamp.
        self._ready.put((-link.priority, link.created, link.id))

    def start_download(self, link_id: str) -> None:
        """(Re)start or queue a link for download."""
        link = self.get(link_id)
        if link is None:
            return
        if self._is_torrent(link):
            if link.status == LinkStatus.ERROR:  # e.g. the add-on was installed since
                link.error = ""
                t = link.extra.get("torrent") or {}
                magnet = t.get("magnet") or (link.url if link.url.lower().startswith("magnet:") else "")
                self._spawn(self._start_torrent, link.id, magnet, t.get("torrent_file", ""))
            elif link.status == LinkStatus.PAUSED:
                self.resume_download(link_id)
            return
        if link.status not in (LinkStatus.WAITING, LinkStatus.PAUSED,
                               LinkStatus.ERROR):
            return
        if link.status == LinkStatus.ERROR:
            link.error = ""
        if not link.direct_url and not link.children:
            # the lookup never finished, so there is nothing to download yet:
            # look the link up again (the workers skip links without a file address)
            link.status = LinkStatus.RESOLVING
            self._persist()
            self._broadcast("grabbed", link)
            self._spawn(self._grab_and_queue, link.id, True)
            return
        link.status = LinkStatus.WAITING
        self._persist()
        self._ready_put(link_id)
        self._broadcast("queued", link)

    def pause_download(self, link_id: str) -> None:
        dl = self._active.get(link_id)
        link = self.get(link_id)
        if not link:
            return
        if self._is_torrent(link):
            if self.torrents.pause(link_id):
                link.status = LinkStatus.PAUSED
                link.speed = 0.0
                self._persist()
                self._broadcast("paused", link)
            return
        if dl:
            link.status = LinkStatus.PAUSED
            link.extra["note"] = "paused by user"
            dl.stop_event.set()
        elif link.status in (LinkStatus.WAITING, LinkStatus.DOWNLOADING):
            link.status = LinkStatus.PAUSED
        self._persist()
        self._broadcast("paused", link)

    def resume_download(self, link_id: str) -> None:
        dl = self._active.get(link_id)
        link = self.get(link_id)
        if not link or link.status != LinkStatus.PAUSED:
            return
        if self._is_torrent(link):
            if self.torrents.resume(link_id):
                link.status = LinkStatus.DOWNLOADING
                link.extra.pop("note", None)
                self._persist()
                self._broadcast("resumed", link)
            return
        # Pause stops the transfer (progress is kept in the .part + chunk
        # map), so resuming always re-queues. If the old transfer is still
        # winding down, its worker re-queues the link when it exits.
        link.status = LinkStatus.WAITING
        link.extra.pop("note", None)
        if dl is not None:
            link.extra["requeue_on_stop"] = True
        else:
            self._ready_put(link_id)
        self._persist()
        self._broadcast("resumed", link)

    def stop_download(self, link_id: str) -> None:
        """Cancel the active transfer (.part kept for resume)."""
        if self._is_torrent(self.get(link_id)):
            return self.pause_download(link_id)
        dl = self._active.get(link_id)
        if dl:
            dl.stop_event.set()
        link = self.get(link_id)
        if link and link.status == LinkStatus.WAITING:
            # queued but not started — just park it
            link.status = LinkStatus.PAUSED
            link.extra["note"] = "stopped by user"
            self._persist()
            self._broadcast("stopped", link)

    def remove_link(self, link_id: str, delete_files: bool = False) -> dict:
        """Remove a link. With ``delete_files`` also delete its own finished and
        partial files (inside the download folder only); returns what was deleted
        and what could not be."""
        link = self.get(link_id)
        if self._is_torrent(link):
            self.torrents.remove(link_id)  # keeps the downloaded files (C6)
        dl = self._active.pop(link_id, None)
        if dl:
            dl.stop_event.set()
        with self._lock:
            link = self._links.pop(link_id, None)
        result = {"deleted": [], "failed": []}
        if link:
            # C6: removing a link never deletes the finished file unless asked,
            # only partial leftovers.
            dest = self.link_root(link)
            candidates = []
            if link.filename:
                candidates.append(dest / (link.filename + ".part"))
            if link.path:
                p = Path(link.path)
                candidates.append(dest / (p.name + ".part"))
                candidates.append(Path(str(p) + ".part"))
                candidates.append(Path(str(p) + ".part.json"))
            if delete_files:
                result = self._delete_link_files(link, dl)
                candidates = []
            for cand in candidates:
                try:
                    if cand and cand.exists():
                        cand.unlink()
                except OSError:
                    pass
        self._save_now()
        self._broadcast("removed")
        return result

    def _own_files(self, link: Link, dl) -> tuple[list[Path], Path]:
        """The link's finished file and the partial/temp files the engine writes
        next to it, and the folder they must stay inside (the link's root)."""
        root = self.link_root(link)
        names = {n for n in (link.filename,
                             str(link.extra.get("download_target_filename", ""))) if n}
        subdir = str(link.extra.get("download_target_subdir", ""))
        folders = [root / subdir] if subdir else [root]
        cands: list[Path] = []  # the finished file is only ever the path the engine recorded
        if link.path:
            p = Path(link.path)
            cands.append(p)
            folders.append(p.parent)
            names.add(p.name)
        if dl is not None:
            names.add(dl.dest.name)
            folders.append(dl.dest_dir)
        for folder in folders:
            for name in names:
                stem = Path(name).stem
                cands += [folder / (name + ".part"), folder / (name + ".part.json"),
                          folder / (stem + ".video.mp4"), folder / (stem + ".audio.m4a")]
        return cands, root

    def _delete_link_files(self, link: Link, dl) -> dict:
        """The link's own files, limited to the link's download folder."""
        from ..filedelete import delete_files
        cands, root = self._own_files(link, dl)
        return delete_files(cands, [root])

    # ------------------------------------------------------- change folder
    def change_folder(self, link_id: str, save_dir: str, move_files: Optional[bool] = None,
                      rename_on_conflict: bool = False) -> dict:
        """Point a link at another folder. ``save_dir`` must already be validated.

        Links that have not written anything just get the new folder. Links with
        partial or finished files need ``move_files`` (True moves the link's own
        files, False leaves them and the download starts again in the new folder);
        without it the result says ``needs_choice``. Moving never overwrites: on a
        name clash nothing moves unless ``rename_on_conflict`` picks a free name.
        """
        import shutil
        link = self.get(link_id)
        if link is None:
            raise KeyError(link_id)
        new_root = Path(save_dir).expanduser().resolve()
        old_root = self.link_root(link)
        if new_root == old_root:
            return {"ok": True, "moved": [], "unchanged": True}
        if self._is_torrent(link):
            return self._change_torrent_folder(link, new_root)
        if link.status == LinkStatus.SKIPPED:
            raise ValueError("this link was skipped by a filter")
        dl = self._active.get(link_id)
        cands, _ = self._own_files(link, dl)
        existing = _existing_unique(cands)
        has_files = bool(existing)
        if has_files and move_files is None:
            return {"ok": False, "needs_choice": True, "files": [c.name for c in existing]}
        was_downloading = link.status == LinkStatus.DOWNLOADING or dl is not None
        if has_files and move_files and was_downloading:
            self.pause_download(link_id)
            for _ in range(100):  # let the transfer wind down so its files are closed
                if link_id not in self._active:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("the download did not stop in time; try again")
            cands, _ = self._own_files(link, None)
            existing = _existing_unique(cands)
        result: dict = {"ok": True, "moved": [], "failed": [], "renamed": ""}
        if has_files and move_files:
            subdir = str(link.extra.get("download_target_subdir", ""))
            target_dir = (new_root / subdir) if subdir else new_root
            plan = self._plan_moves(link, existing, old_root, target_dir, rename_on_conflict)
            if plan.get("conflicts"):
                if was_downloading:
                    self.resume_download(link_id)
                return {"ok": False, "conflicts": plan["conflicts"]}
            target_dir.mkdir(parents=True, exist_ok=True)
            for src, dst in plan["pairs"]:
                try:
                    if os.path.lexists(dst):  # checked again right before: never overwrite
                        raise FileExistsError(17, "a file with that name already exists", str(dst))
                    shutil.move(str(src), str(dst))
                    result["moved"].append(str(dst))
                except OSError as exc:
                    result["failed"].append({"path": str(src), "reason": exc.strerror or str(exc)})
            if plan.get("new_name"):
                result["renamed"] = plan["new_name"]
                link.filename = plan["new_name"]
                link.extra["download_target_filename"] = plan["new_name"]
            if link.path:
                for src, dst in plan["pairs"]:
                    if os.path.normcase(str(src)) == os.path.normcase(link.path):
                        link.path = str(dst)
        link.extra["save_dir"] = str(new_root)
        self._persist()
        self._broadcast("folder", link)
        if was_downloading and link.status == LinkStatus.PAUSED:
            self.resume_download(link_id)
        return result

    def _plan_moves(self, link: Link, existing: list[Path], old_root: Path, target_dir: Path,
                    rename: bool) -> dict:
        """Which files go where; only regular files inside the old root move."""
        real_root = Path(os.path.realpath(old_root))
        safe: list[Path] = []
        for c in existing:
            real = Path(os.path.realpath(c.parent)) / c.name
            try:
                real.relative_to(real_root)
            except ValueError:
                continue  # not the link's own place: left alone, like delete
            if c.is_symlink() or not c.is_file():
                continue
            safe.append(c)
        old_name = str(link.extra.get("download_target_filename") or link.filename or "")

        def mapped(c: Path, name: str) -> str:
            if old_name and c.name.startswith(old_name):
                return name + c.name[len(old_name):]
            stem_old = Path(old_name).stem
            if old_name and c.name.startswith(stem_old + "."):
                return Path(name).stem + c.name[len(stem_old):]
            return c.name
        new_name = old_name
        pairs = [(c, target_dir / mapped(c, new_name)) for c in safe]
        clashes = [dst for _, dst in pairs if os.path.lexists(dst)]
        if clashes and rename and old_name:
            base = Path(old_name)
            for i in range(1, 10000):
                trial = f"{base.stem}_{i}{base.suffix}"
                trial_pairs = [(c, target_dir / mapped(c, trial)) for c in safe]
                if not any(os.path.lexists(d) for _, d in trial_pairs):
                    pairs, new_name, clashes = trial_pairs, trial, []
                    break
        if clashes:
            return {"conflicts": [d.name for d in clashes]}
        return {"pairs": pairs, "new_name": new_name if new_name != old_name else ""}

    def _change_torrent_folder(self, link: Link, new_root: Path) -> dict:
        """libtorrent moves the data itself (move_storage); before the torrent starts
        only the folder it will be added to changes."""
        info = link.extra.get("torrent") or {}
        if self.torrents.handle_for(link.id) is not None:
            if not self.torrents.move_storage(link.id, new_root):
                raise ValueError("this torrent's folder can only be changed before it starts")
            info["save_path"] = str(new_root)
            link.extra["torrent"] = info
        link.extra["save_dir"] = str(new_root)
        self._persist()
        self._broadcast("folder", link)
        return {"ok": True, "moved": [], "failed": [], "renamed": ""}

    def clear_finished(self) -> int:
        with self._lock:
            ids = [l.id for l in self._links.values()
                   if l.status in (LinkStatus.FINISHED, LinkStatus.SKIPPED)]
            for i in ids:
                del self._links[i]
        n = len(ids)
        if n:
            self._save_now()
            self._broadcast("cleared")
        return n

    # --------------------------------------------- captcha / login / paywall
    def submit_captcha(self, link_id: str, code: str) -> None:
        """User submitted a captcha solution (manual or via service).
        Re-runs the plugin; ctx.manual_code short-circuits ctx.solve()."""
        link = self.get(link_id)
        if not link or link.status != LinkStatus.CAPTCHA:
            return
        link.status = LinkStatus.RESOLVING
        link.extra.pop("captcha_error", None)
        self._persist()
        self._broadcast("grabbing", link)

        def retry():
            try:
                plugin = self._plugin_for(link)
                ctx = self._ctx_for(plugin)
                ctx.manual_code = (code or "").strip()
                self._resolve(link, plugin, ctx)
                if link.status == LinkStatus.WAITING and link.direct_url:
                    self._ready_put(link.id)
                    self._broadcast("queued", link)
            except CaptchaRequired:
                link.status = LinkStatus.CAPTCHA
                link.extra["captcha_error"] = "captcha still required"
            except Exception as e:
                link.status = LinkStatus.CAPTCHA
                link.extra["captcha_error"] = str(e)
            self._persist()
            self._broadcast("grabbed", link)

        self._spawn(retry)

    def add_login(self, link_id: str, username: str, password: str) -> None:
        """Store site credentials, then re-run the plugin (ctx.account_for
        will hand them to the plugin)."""
        link = self.get(link_id)
        if not link:
            return
        host = urlparse(link.url).netloc.lower()
        self.accounts.add(host, username, password)

        def retry():
            try:
                plugin = self._plugin_for(link)
                ctx = self._ctx_for(plugin)
                self._resolve(link, plugin, ctx)
                if link.status == LinkStatus.WAITING and link.direct_url:
                    self._ready_put(link.id)
                    self._broadcast("queued", link)
            except LoginRequired as e:
                link.login_hint = e.hint or link.login_hint
            except Exception as e:
                link.status = LinkStatus.ERROR
                link.error = str(e)
            self._persist()
            self._broadcast("grabbed", link)

        self._spawn(retry)

    def accept_paywall(self, link_id: str) -> None:
        """User acknowledged the paywall notice — re-resolve anyway
        (some 'paywalls' are soft limits that work on retry/with cookies)."""
        link = self.get(link_id)
        if not link or link.status != LinkStatus.PAYWALL:
            return
        link.status = LinkStatus.RESOLVING
        self._persist()

        def retry():
            try:
                plugin = self._plugin_for(link)
                ctx = self._ctx_for(plugin)
                self._resolve(link, plugin, ctx)
                if link.status == LinkStatus.WAITING and link.direct_url:
                    self._ready_put(link.id)
                    self._broadcast("queued", link)
            except Exception as e:
                link.status = LinkStatus.ERROR
                link.error = str(e)
            self._persist()
            self._broadcast("grabbed", link)

        self._spawn(retry)

    # ---------------------------------------------------------------- worker
    def _worker(self, index=0) -> None:
        while True:
            if index >= max(1, int(self.settings.max_parallel or 1)):
                time.sleep(0.5)
                continue
            try:
                _, _, link_id = self._ready.get(timeout=1.0)
            except queue.Empty:
                continue
            link = self.get(link_id)
            if link is None or link.status != LinkStatus.WAITING:
                continue
            if not link.direct_url:
                continue
            with self._lock:
                # torrents run inside libtorrent: they never use an HTTP download slot
                running = [l for l in self._links.values()
                           if l.status == LinkStatus.DOWNLOADING and l.plugin != "torrent"]
                reason = self.flow.gate(link, running)
                if reason:
                    self._deferred.add(link.id)
                elif link.status == LinkStatus.WAITING:
                    link.status = LinkStatus.DOWNLOADING  # reserve the slot atomically
                else:
                    continue
            if reason:
                if link.extra.get("wait_reason") != reason:
                    link.extra["wait_reason"] = reason
                    self._persist()
                    self._broadcast("waiting", link)
                continue
            link.extra.pop("wait_reason", None)
            self._run_download(link, previous_status=LinkStatus.WAITING)

    # --------------------------------------------------------- W3-W5 ticker
    def _flow_loop(self) -> None:
        """Once a second: apply the effective speed limit, follow schedule
        pause windows, and give deferred downloads another chance."""
        while self._started:
            try:
                self._flow_tick()
            except Exception:
                log.debug("flow tick failed", exc_info=True)
            time.sleep(1.0)

    def _flow_tick(self) -> None:
        self._global_limiter.set_rate(self.flow.effective_limit_bps())
        self.torrents.apply_limits()  # W11: same limits for torrents
        paused = self.flow.downloads_paused_by_schedule()
        if paused and not self._schedule_paused:
            for l in self.all_links():
                if l.status == LinkStatus.DOWNLOADING:
                    l.extra["paused_by_schedule"] = True
                    self.pause_download(l.id)
                    l.extra["note"] = "paused by the schedule"
        elif not paused and self._schedule_paused:
            for l in self.all_links():
                if l.extra.pop("paused_by_schedule", False) and l.status == LinkStatus.PAUSED:
                    self.resume_download(l.id)
        self._schedule_paused = paused
        with self._lock:
            again, self._deferred = self._deferred, set()
        for lid in again:
            self._ready_put(lid)

    def settings_changed(self) -> list[str]:
        """Re-read flow-control settings (called after POST /api/settings)."""
        errors = self.flow.reload() + self.net.reload()
        self._global_limiter.set_rate(self.flow.effective_limit_bps())
        return errors

    def _run_download(self, link: Link, previous_status=None) -> None:
        reservation = None
        try:
            previous_status = previous_status or link.status
            link.status = LinkStatus.DOWNLOADING
            link.error = ""
            self._persist()
            self._broadcast("downloading", link)
            url = link.direct_url or link.url
            from .packagizer import plan_packagizer, safe_filename, safe_subdir
            root = self.link_root(link)
            saved_name = str(link.extra.get("download_target_filename", ""))
            saved_subdir = str(link.extra.get("download_target_subdir", ""))
            if saved_name:
                plan = dict(link.extra.get("packagizer", {}))
                plan.update({"filename": safe_filename(saved_name),
                             "subdir": safe_subdir(saved_subdir)})
            else:
                source_name = link.filename or Path(urlparse(link.url).path).name
                plan = plan_packagizer(self.settings.packagizer_rules,
                                       link.url, source_name, root)
                if not plan.get("matched"):
                    # W4: categories decide the folder when no Packagizer rule matched
                    link.extra["category"] = self.flow.category_for(plan["filename"])
                    cat_dir = self.flow.category_subdir(plan["filename"])
                    if cat_dir:
                        plan["subdir"] = safe_subdir(cat_dir)
            dest = (root / plan["subdir"]).resolve()
            dest.relative_to(root)
            filename = safe_filename(plan["filename"])
            reusable_partial = bool(saved_name or previous_status == LinkStatus.PAUSED
                                    or link.downloaded > 0)
            base_path = Path(filename)
            with self._lock:
                for index in range(10000):
                    candidate = (filename if index == 0 else
                                 f"{base_path.stem}_{index}{base_path.suffix}")
                    key = (str(dest).casefold(), candidate.casefold())
                    final_path = dest / candidate
                    partial_path = dest / (candidate + ".part")
                    if (key not in self._reserved_downloads
                            and not final_path.exists()
                            and (reusable_partial or not partial_path.exists())):
                        reservation = key
                        self._reserved_downloads.add(key)
                        filename = candidate
                        break
                else:
                    raise RuntimeError("too many download filename collisions")
            link.filename = filename
            link.extra["download_target_filename"] = filename
            link.extra["download_target_subdir"] = plan["subdir"]
            if plan.get("matched"):
                link.extra["packagizer"] = {
                    "rule_id": plan["rule_id"], "rule_name": plan["rule_name"],
                    "filename": filename, "subdir": plan["subdir"],
                }
            self._persist()
            if self._is_hls(link, url):
                path = self._download_hls(link, url, dest / filename)
            elif self._is_dash(link, url):
                path = self._download_dash(link, url, dest / filename)
            else:
                from ..extrules import browser_request_extras
                b_headers, b_cookies = browser_request_extras(link.extra, url)
                c_headers, c_cookies = self._clearance_extras(url)
                c_cookies.update(self._account_cookies(url))
                dl = LinkDownload(link, url, dest,
                                  headers={**b_headers, **c_headers,
                                           **(link.direct_headers or {})},
                                  settings=self.settings,
                                  cookies={**dict(self.client._cookies_for(url) or {}),
                                           **b_cookies, **c_cookies},
                                  rate_limiter=self._global_limiter,
                                  net=self.net,
                                  on_tick=lambda d: self._on_tick(link, d))
                with self._lock:
                    self._active[link.id] = dl
                acct = link.extra.get("account")
                if acct:
                    self.account_policy.start(acct)
                try:
                    path = dl.run()
                finally:
                    with self._lock:
                        self._active.pop(link.id, None)
                    try:  # bytes after the last progress tick
                        self._count_bytes(link.id, dl, dl.progress()[1])
                    except Exception:
                        pass
                    self._tick_done.pop(link.id, None)
                    if acct:
                        self.account_policy.finish(acct)
            path = self._finish_media(link, Path(path))
            path = self._plugin_finalize(link, Path(path))
            link.path = str(path)
            self._verify_hash(link, Path(path))
            if self.settings.auto_extract_archives:
                self._extract(link, Path(path))
            try:
                link.size = Path(path).stat().st_size
            except OSError:
                pass
            link.downloaded = link.size or link.downloaded
            link.status = LinkStatus.FINISHED
            link.extra.pop("link_refreshed", None)
            link.finished = time.time()
            link.speed = 0.0
            link.progress = 1.0
            self._retries.pop(link.id, None)
            self._broadcast("finished", link)
            if self.virus_scan.enabled() or self.virus_scan.vt_enabled():  # W10: never blocks the queue
                self._spawn(self._scan_link, link.id)
            if str(path).lower().endswith(".torrent") and getattr(self.settings, "torrent_open_files", True):
                self.add_torrent_file(str(path), title=Path(path).stem)  # W11
        except CaptchaRequired as e:
            # W2: a challenge page instead of the file
            if not self._handle_challenge(link, e, phase="download"):
                self._persist()
                self._broadcast("grabbed", link)
        except DownloadError as e:
            msg = str(e)
            if "cancel" in msg.lower():
                if link.status == LinkStatus.WAITING:
                    pass  # user resumed while it was stopping
                elif link.status != LinkStatus.PAUSED:
                    link.status = LinkStatus.PAUSED
                    link.extra["note"] = "stopped by user"
                # already PAUSED: keep its note ("paused by user", app closed)
                self._persist()
                self._broadcast("stopped", link)
            elif self._refresh_expired_link(link, msg):
                pass  # the plugin is asked for a fresh link; the download is requeued
            else:
                self._fail(link, msg, e.transient)
        except Exception as e:
            log.exception("download crashed: %s", link.url)
            self._fail(link, str(e), False)
        finally:
            if reservation:
                with self._lock:
                    self._reserved_downloads.discard(reservation)
            # AFTER the reservation is discarded
            if link.extra.pop("requeue_on_stop", False) and link.status == LinkStatus.WAITING:
                self._ready_put(link.id)
            self._persist()

    def _finish_media(self, link: Link, path: Path) -> Path:
        """M9d: merge a separate audio part into a finished video download."""
        from ..media.mergejob import audio_part, merge_job
        if audio_part(link.extra) is None:
            return Path(path)
        headers = dict(link.direct_headers or {})

        def fetch(url: str, dest: Path) -> None:
            self._fetch_media_part(url, Path(dest), headers)

        return merge_job(Path(path), link.extra, fetch,
                         ffmpeg_path=self.settings.media_ffmpeg_path)

    def _fetch_media_part(self, url: str, dest: Path, headers: dict) -> None:
        """Download one media part (e.g. the audio track) to dest."""
        import shutil
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        from ..tls import urlopen
        with urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh, 1024 * 1024)

    @staticmethod
    def _is_hls(link: Link, url: str) -> bool:
        """M9f: True for HLS streams (yt-dlp protocol hint or a .m3u8 URL)."""
        proto = str(link.extra.get("media_protocol", "") or "").lower()
        if proto == "hls" or proto.startswith("m3u8"):
            return True
        return urlparse(url).path.lower().endswith(".m3u8")

    def _download_hls(self, link: Link, url: str, path: Path) -> Path:
        """M9f: download an HLS stream segment by segment into path."""
        from ..media.hlsdl import HlsError, download_hls
        headers = dict(link.direct_headers or {})

        def fetch_bytes(u):
            return self._fetch_url(u, headers)

        def fetch_text(u):
            return self._fetch_url(u, headers).decode("utf-8", "replace")

        def progress(i, total, written):
            link.downloaded = written
            link.progress = min(i / total, 1.0)
            self._broadcast("progress", link)

        def cancel():
            return link.status != LinkStatus.DOWNLOADING

        max_height = int(getattr(self.settings, "media_max_height", 0) or 0)
        try:
            info = download_hls(url, str(path), fetch_bytes, fetch_text,
                                max_height=max_height,
                                on_progress=progress, cancel=cancel)
        except HlsError as exc:
            if str(exc) == "cancelled":
                raise DownloadError("cancelled by user") from exc
            raise DownloadError("HLS: %s" % exc) from exc
        link.extra["hls"] = {"segments": info["segments"], "bytes": info["bytes"]}
        return Path(path)

    @staticmethod
    def _is_dash(link: Link, url: str) -> bool:
        """M9g: True for DASH streams (yt-dlp protocol hint or a .mpd URL)."""
        proto = str(link.extra.get("media_protocol", "") or "").lower()
        if proto in ("dash", "http_dash_segments"):
            return True
        return urlparse(url).path.lower().endswith(".mpd")

    def _download_dash(self, link: Link, url: str, path: Path, merge_fn=None) -> Path:
        """M9g: download a static DASH stream, then merge video and audio with ffmpeg."""
        from ..media import ffmpeg as media_ffmpeg
        from ..media.dashdl import DashError, download_dash
        headers = dict(link.direct_headers or {})

        def fetch_bytes(u):
            return self._fetch_url(u, headers)

        def fetch_text(u):
            return self._fetch_url(u, headers).decode("utf-8", "replace")

        def progress(i, total, written):
            link.downloaded = written
            link.progress = min(i / total, 1.0)
            self._broadcast("progress", link)

        def cancel():
            return link.status != LinkStatus.DOWNLOADING

        path = Path(path)
        video_tmp = path.with_name(path.stem + ".video.mp4")
        audio_tmp = path.with_name(path.stem + ".audio.m4a")
        max_height = int(getattr(self.settings, "media_max_height", 0) or 0)
        try:
            info = download_dash(url, str(video_tmp), str(audio_tmp), fetch_bytes, fetch_text,
                                 max_height=max_height, on_progress=progress, cancel=cancel)
        except DashError as exc:
            video_tmp.unlink(missing_ok=True)
            audio_tmp.unlink(missing_ok=True)
            if str(exc) == "cancelled":
                raise DownloadError("cancelled by user") from exc
            raise DownloadError("DASH: %s" % exc) from exc
        link.extra["dash"] = {"segments": info["segments"], "bytes": info["bytes"]}
        if info["video"] is None:
            final = path.with_suffix(".m4a")
            os.replace(audio_tmp, final)
            return final
        final = path.with_suffix(".mp4")
        if info["audio"] is None:
            os.replace(video_tmp, final)
            return final
        merge_fn = merge_fn or media_ffmpeg.merge
        try:
            merge_fn(str(video_tmp), str(audio_tmp), str(final),
                     ffmpeg=self.settings.media_ffmpeg_path)
        except (media_ffmpeg.MediaUnavailable, media_ffmpeg.MergeError, OSError) as exc:
            link.extra["media_merge"] = {"status": "failed", "message": str(exc)[:300]}
            os.replace(video_tmp, final)
            return final
        video_tmp.unlink(missing_ok=True)
        audio_tmp.unlink(missing_ok=True)
        link.extra["media_merge"] = {"status": "ok"}
        return final

    def _fetch_url(self, url: str, headers: dict) -> bytes:
        """Fetch one URL (an HLS playlist or segment) into memory."""
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        from ..tls import urlopen
        with urlopen(req, timeout=60) as resp:
            return resp.read()

    # ------------------------------------------------------------ W11 reconnect
    def reconnector(self):
        from ..reconnect import Reconnector, config_from_dict
        raw = getattr(self.settings, "reconnect", None) or {}
        try:
            cfg = config_from_dict(raw)
        except ValueError:
            return None
        key = repr(sorted(raw.items()))
        if getattr(self, "_reconnector_key", None) != key:
            self._reconnector_key = key
            self._reconnector = Reconnector(cfg, lambda: self._run_reconnect_command(raw),
                                            lambda: self._public_ip(raw))
        return self._reconnector

    def _run_reconnect_command(self, raw: dict) -> None:
        import shlex
        import subprocess
        cmd = str(raw.get("command") or "").strip()
        if not cmd:
            raise RuntimeError("no reconnect command configured")
        # Windows takes a command line string as is; POSIX gets an argv list
        argv = cmd if os.name == "nt" else shlex.split(cmd)
        subprocess.run(argv, check=True, timeout=120, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(max(0, float(raw.get("settle_seconds", 15) or 0)))

    def _public_ip(self, raw: dict) -> str:
        url = str(raw.get("ip_check_url") or "https://api.ipify.org")
        return self.client.get(url, timeout=15, detect_challenges=False).text.strip()[:64]

    def _maybe_reconnect(self, msg: str) -> None:
        """A host asked us to wait (429 or "wait N minutes"): reset the IP when allowed."""
        import re
        rc = self.reconnector()
        if rc is None or not rc.config.enabled:
            return
        m = re.search(r"(\d+)\s*(second|sec|minute|min|hour)", msg, re.I)
        if m:
            wait = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)[0].lower()]
        elif "429" in msg or "too many" in msg.lower():
            wait = rc.config.min_wait_seconds
        else:
            return
        with self._lock:
            active = len(self._active)
        if not rc.should_reconnect(wait, active):
            return

        def run():
            result = rc.reconnect()
            self.on_change("reconnect", {"ok": result.ok, "old_ip": result.old_ip,
                                         "new_ip": result.new_ip, "error": result.error})
            if result.ok:  # retry what was waiting right away
                for l in self.all_links():
                    if l.status == LinkStatus.WAITING and l.error.startswith("retry"):
                        self._ready_put(l.id)

        self._spawn(run)

    def _fail(self, link: Link, msg: str, transient: bool) -> None:
        self._maybe_reconnect(msg)
        n = self._retries.get(link.id, 0)
        maxr = max(0, self.settings.retry_max)
        if transient and n < maxr:
            self._retries[link.id] = n + 1
            link.status = LinkStatus.WAITING
            link.error = f"retry {n + 1}/{maxr}: {msg}"
            link.attempts = n + 1

            delay = self.flow.retry.delay_for(n + 1)

            def delayed():
                time.sleep(delay)
                if self.get(link.id) is not None:
                    self._ready_put(link.id)

            self._spawn(delayed)
            self._broadcast("retrying", link)
        else:
            link.status = LinkStatus.ERROR
            link.error = msg
            self._broadcast("error", link)

    def _count_bytes(self, link_id: str, dl: LinkDownload, done: int) -> None:
        """Feed newly transferred bytes to the data cap (W5)."""
        prev = self._tick_done.get(link_id)
        if prev is None:
            prev = int(getattr(dl, "start_done", 0) or 0)
        if done > prev:
            self.flow.record_bytes(done - prev)
            acct = (dl.link.extra or {}).get("account")
            if acct:
                self.account_policy.record(acct, done - prev)  # W9 daily quota
        self._tick_done[link_id] = max(prev, done)

    def _on_tick(self, link: Link, dl: LinkDownload) -> None:
        total, done, speed = dl.progress()
        self._count_bytes(link.id, dl, done)
        link.downloaded = done
        if total:
            link.size = total
            link.progress = min(done / total, 1.0)
            if speed > 0:
                link.eta = (total - done) / speed
        link.speed = speed
        self._broadcast("progress", link)
        self._persist()

    # -------------------------------------------------------------- persist
    def _persist(self) -> None:
        """Debounced save — link state changes fast during downloads."""
        now = time.time()
        if now - self._last_persist < 1.0:
            if not self._persist_scheduled:
                self._persist_scheduled = True

                def later():
                    self._persist_scheduled = False
                    self._save_now()

                threading.Thread(target=later, daemon=True,
                                 name="engine-persist").start()
            return
        self._save_now()

    def _save_now(self) -> None:
        # Debounced worker saves can overlap shutdown saves. Serialize the
        # shared temporary file and atomic replacement, especially on Windows
        # where concurrent rename/open operations fail with WinError 32.
        with self._persist_lock:
            self._last_persist = time.time()
            try:
                with self._lock:
                    # Use SessionDB instead of links.json
                    self._session().replace_all([{"id": "queue", "data": {}, "links": [{"id": l.id, "data": json.loads(l.model_dump_json())} for l in self._links.values()]}])
            except Exception as e:
                log.warning("persist failed: %s", e, exc_info=True)

    def _load_links(self) -> None:
        try:
            # Try to load from session DB first
            data = self._session().load()
            # Get links from "queue" package if it exists  
            queue_package = next((pkg for pkg in data if pkg["id"] == "queue"), None)
            migrating = False
            if queue_package is not None:
                links_data = queue_package["links"]
            elif not data and self.links_file.exists():
                # Migration from the legacy links.json file
                legacy = json.loads(self.links_file.read_text(encoding="utf-8"))
                links_data = [{"id": d.get("id"), "data": d} for d in legacy
                              if isinstance(d, dict)]
                migrating = True
            else:
                links_data = []

            for d in links_data:
                try:
                    l = Link(**d["data"])
                except Exception:
                    log.warning("skipping corrupt link entry %r", d.get("id"))
                    continue
                if l.status == LinkStatus.DOWNLOADING:
                    l.status = LinkStatus.PAUSED
                    l.extra["note"] = "was downloading when app closed"
                elif l.status == LinkStatus.RESOLVING:
                    l.status = LinkStatus.ERROR
                    l.error = "was resolving when app closed — remove and re-add the URL"
                with self._lock:
                    self._links[l.id] = l
            if migrating:
                self._save_now()
                self.links_file.replace(
                    self.links_file.with_name(self.links_file.name + ".migrated"))
                log.info("migrated links.json to session.db")
            log.info("loaded %d links from disk", len(self._links))
        except Exception:
            log.warning("failed to load links", exc_info=True)
