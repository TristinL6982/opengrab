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
import queue
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..accounts import AccountManager
from ..captcha.chain import SolverChain
from ..filters import FilterManager
from ..http_client import HttpClient
from ..models import Link, LinkStatus
from ..plugins.base import (CaptchaRequired, LoginRequired, PaywallRequired,
                            PluginContext, PluginError)
from ..plugins.registry import PluginRegistry
from ..settings import Settings, app_data_dir, user_plugins_dir
from .downloader import DownloadError, LinkDownload

log = logging.getLogger("opengrab.engine")


class DownloadManager:
    def __init__(self, settings: Settings, on_change=None):
        self.settings = settings
        self.data_dir = app_data_dir()
        self.links_file = self.data_dir / "links.json"
        self.on_change = on_change or (lambda kind, payload: None)

        self._links: dict[str, Link] = {}
        self._lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self._ready: "queue.Queue[str]" = queue.Queue()
        self._active: dict[str, LinkDownload] = {}

        self.client = HttpClient(settings)
        self.accounts = AccountManager()
        self.filters = FilterManager()
        self.registry = PluginRegistry()
        self.captcha_chain = SolverChain(settings, self.client)
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
        n = self.settings.max_parallel or 3
        for i in range(n):
            t = threading.Thread(target=self._worker, name=f"worker-{i}",
                                 daemon=True)
            t.start()
            self._workers.append(t)
        self._started = True
        log.info("engine started (%d workers)", n)

    def shutdown(self) -> None:
        for dl in list(self._active.values()):
            try:
                dl.pause()
            except Exception:
                pass
        with self._lock:
            for l in self._links.values():
                if l.status == LinkStatus.DOWNLOADING:
                    l.status = LinkStatus.PAUSED
                    l.extra["note"] = "was downloading when app closed"
        self._save_now()
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
                filename: str = "") -> Link:
        """Register a URL and run the grab phase (async).  Returns the Link
        immediately (status RESOLVING, or SKIPPED if a filter excludes it)."""
        url = (url or "").strip()
        if not url:
            raise ValueError("empty url")
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
                    priority=priority, status=LinkStatus.RESOLVING)
        with self._lock:
            self._links[link.id] = link
        self._persist()
        self._broadcast("grabbed", link)

        self._spawn(self._grab_and_queue, link.id, auto_download)
        return link

    def _spawn(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True,
                         name="engine-task").start()

    def _ctx(self) -> PluginContext:
        return PluginContext(client=self.client, settings=self.settings,
                             accounts=self.accounts,
                             captcha_chain=self.captcha_chain,
                             logger=log)

    def _pick_plugin(self, link: Link):
        plugins = self.registry.for_url(link.url)
        if not plugins:
            raise PluginError(f"no plugin supports this url: {link.url}")
        return plugins[0]

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
            plugin = self._pick_plugin(link)
            link.plugin = plugin.id
            ctx = self._ctx()
            try:
                g = plugin.grab(link.url, ctx)
            except (NotImplementedError, PluginError):
                g = None
            if g is not None:
                self._apply_grab(link, plugin.id, g)
            if link.status not in (LinkStatus.CAPTCHA, LinkStatus.LOGIN,
                                   LinkStatus.PAYWALL, LinkStatus.SKIPPED):
                self._resolve(link, plugin, ctx)
        except CaptchaRequired as e:
            link.status = LinkStatus.CAPTCHA
            link.captcha_image = e.image_b64
            link.captcha_kind = e.kind
            link.extra.update({
                "captcha_site_url": e.site_url,
                "captcha_sitekey": e.sitekey,
                "captcha_image_url": (e.extra or {}).get("image_url", ""),
            })
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

    def _apply_grab(self, link: Link, plugin_id: str, g) -> None:
        link.plugin = plugin_id
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
                                 auto_download=auto, title=t)
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
            link.status = LinkStatus.WAITING
            return
        if link.status in (LinkStatus.CAPTCHA, LinkStatus.LOGIN,
                           LinkStatus.PAYWALL, LinkStatus.SKIPPED):
            return
        r = plugin.resolve(link.url, ctx)
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
        elif link.children:
            # package parent: the children carry the download
            link.status = LinkStatus.WAITING
        else:
            raise PluginError(
                f"plugin '{getattr(plugin, 'id', '?')}' produced no direct "
                "url and no children")

    # ------------------------------------------------------------- download
    def _ready_put(self, link_id: str) -> None:
        self._ready.put(link_id)

    def start_download(self, link_id: str) -> None:
        """(Re)start or queue a link for download."""
        link = self.get(link_id)
        if link is None:
            return
        if link.status not in (LinkStatus.WAITING, LinkStatus.PAUSED,
                               LinkStatus.ERROR):
            return
        if link.status == LinkStatus.ERROR:
            link.error = ""
        link.status = LinkStatus.WAITING
        self._persist()
        self._ready_put(link_id)
        self._broadcast("queued", link)

    def pause_download(self, link_id: str) -> None:
        dl = self._active.get(link_id)
        link = self.get(link_id)
        if not link:
            return
        if dl:
            dl.pause()
            link.status = LinkStatus.PAUSED
        elif link.status in (LinkStatus.WAITING, LinkStatus.DOWNLOADING):
            link.status = LinkStatus.PAUSED
        self._persist()
        self._broadcast("paused", link)

    def resume_download(self, link_id: str) -> None:
        dl = self._active.get(link_id)
        link = self.get(link_id)
        if not link or link.status != LinkStatus.PAUSED:
            return
        if dl:
            # worker thread still alive — unpause in place
            dl.resume()
            link.status = LinkStatus.DOWNLOADING
        else:
            # app restarted mid-download: .part file resumes from disk
            link.status = LinkStatus.WAITING
            self._ready_put(link_id)
        self._persist()
        self._broadcast("resumed", link)

    def stop_download(self, link_id: str) -> None:
        """Cancel the active transfer (.part kept for resume)."""
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

    def remove_link(self, link_id: str) -> None:
        dl = self._active.pop(link_id, None)
        if dl:
            dl.stop_event.set()
        with self._lock:
            link = self._links.pop(link_id, None)
        if link:
            dest = Path(self.settings.download_dir)
            candidates = []
            if link.filename:
                candidates.append(dest / (link.filename + ".part"))
            if link.path:
                p = Path(link.path)
                candidates.append(dest / (p.name + ".part"))
                candidates.append(p)
            for cand in candidates:
                try:
                    if cand and cand.exists():
                        cand.unlink()
                except OSError:
                    pass
        self._save_now()
        self._broadcast("removed")

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
                ctx = self._ctx()
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
                ctx = self._ctx()
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
                ctx = self._ctx()
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
    def _worker(self) -> None:
        while True:
            try:
                link_id = self._ready.get(timeout=1.0)
            except queue.Empty:
                continue
            link = self.get(link_id)
            if link is None or link.status != LinkStatus.WAITING:
                continue
            if not link.direct_url:
                continue
            self._run_download(link)

    def _run_download(self, link: Link) -> None:
        try:
            link.status = LinkStatus.DOWNLOADING
            link.error = ""
            self._persist()
            self._broadcast("downloading", link)
            url = link.direct_url or link.url
            dest = Path(self.settings.download_dir)
            dl = LinkDownload(link, url, dest,
                              headers=link.direct_headers,
                              settings=self.settings,
                              cookies=self.client._cookies_for(url),
                              on_tick=lambda d: self._on_tick(link, d))
            with self._lock:
                self._active[link.id] = dl
            try:
                path = dl.run()
            finally:
                with self._lock:
                    self._active.pop(link.id, None)
            link.path = str(path)
            if self.settings.auto_extract_archives:
                from .archives import extract_archive, is_supported_archive
                if is_supported_archive(Path(path)):
                    try:
                        link.extra["archive_extraction"] = extract_archive(
                            Path(path),
                            max_files=max(1, min(self.settings.archive_max_files, 100000)),
                            max_bytes=max(1, self.settings.archive_max_expanded_gib)
                            * 1024 * 1024 * 1024,
                            password_file=self.settings.archive_password_file,
                        )
                    except Exception as exc:  # preserve the completed download
                        link.extra["archive_extraction"] = {
                            "status": "failed", "message": str(exc)[:500]
                        }
            try:
                link.size = Path(path).stat().st_size
            except OSError:
                pass
            link.downloaded = link.size or link.downloaded
            link.status = LinkStatus.FINISHED
            link.finished = time.time()
            link.speed = 0.0
            link.progress = 1.0
            self._retries.pop(link.id, None)
            self._broadcast("finished", link)
        except DownloadError as e:
            msg = str(e)
            if "cancel" in msg.lower():
                link.status = LinkStatus.PAUSED
                link.extra["note"] = "stopped by user"
                self._persist()
                self._broadcast("stopped", link)
            else:
                self._fail(link, msg, e.transient)
        except Exception as e:
            log.exception("download crashed: %s", link.url)
            self._fail(link, str(e), False)
        finally:
            self._persist()

    def _fail(self, link: Link, msg: str, transient: bool) -> None:
        n = self._retries.get(link.id, 0)
        maxr = max(0, self.settings.retry_max)
        if transient and n < maxr:
            self._retries[link.id] = n + 1
            link.status = LinkStatus.WAITING
            link.error = f"retry {n + 1}/{maxr}: {msg}"
            link.attempts = n + 1

            def delayed():
                time.sleep(min(2 ** n, 60))
                if self.get(link.id) is not None:
                    self._ready_put(link.id)

            self._spawn(delayed)
            self._broadcast("retrying", link)
        else:
            link.status = LinkStatus.ERROR
            link.error = msg
            self._broadcast("error", link)

    def _on_tick(self, link: Link, dl: LinkDownload) -> None:
        total, done, speed = dl.progress()
        link.downloaded = done
        if total:
            link.size = total
            link.progress = min(done / total, 1.0)
            if speed > 0:
                link.eta = (total - done) / speed
        link.speed = speed
        self._broadcast("progress", link)

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
                    data = [json.loads(l.model_dump_json())
                            for l in self._links.values()
                            if l.status != LinkStatus.DOWNLOADING]
                tmp = self.links_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
                tmp.replace(self.links_file)
            except Exception:
                log.warning("persist failed", exc_info=True)

    def _load_links(self) -> None:
        if not self.links_file.exists():
            return
        try:
            data = json.loads(self.links_file.read_text(encoding="utf-8"))
            for d in data:
                try:
                    l = Link(**d)
                except Exception:
                    log.warning("skipping corrupt link entry %r", d.get("id"))
                    continue
                if l.status == LinkStatus.DOWNLOADING:
                    l.status = LinkStatus.PAUSED
                    l.extra["note"] = "was downloading when app closed"
                elif l.status == LinkStatus.RESOLVING:
                    l.status = LinkStatus.ERROR
                    l.error = "was resolving when app closed — " \
                              "remove and re-add the URL"
                with self._lock:
                    self._links[l.id] = l
            log.info("loaded %d links from disk", len(self._links))
        except Exception:
            log.warning("failed to load links.json", exc_info=True)
