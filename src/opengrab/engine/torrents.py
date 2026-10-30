"""W11: BitTorrent in the running app (optional libtorrent add-on).

Wires three M11 modules that had tests but no caller:

* torrent   - magnet parsing, TorrentSettings (DHT/PEX/LSD, sequential,
              ratio and seed-time limits), file priorities and TorrentEngine
              over an injected session. ``LtSession`` is that session.
* trackers  - TrackerManager: default public trackers merged into every
              torrent, announce health from libtorrent alerts.
* portmap   - PortMapper over libtorrent's own UPnP / NAT-PMP mapping.

A torrent is a Link (plugin "torrent") the workers never touch: this
service owns it, polls libtorrent once a second and maps its state onto
the link (RESOLVING while fetching metadata, DOWNLOADING, FINISHED).
libtorrent is an add-on (addons.py "torrent"); without it a magnet link
becomes an error that says how to install it.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from .. import portmap as pm
from .. import torrent as tor
from ..models import Link, LinkStatus
from ..trackers import TrackerManager

log = logging.getLogger("opengrab.torrents")

NOT_INSTALLED = ("BitTorrent support is an optional add-on. Install it in Settings, "
                 "BitTorrent, then choose Retry.")


def libtorrent_available() -> bool:
    from .. import addons
    addons.activate_installed()
    return addons.importable("libtorrent")


class LtHandle:
    def __init__(self, handle):
        self.h = handle

    def set_priorities(self, priorities: list) -> None:
        self.h.prioritize_files(priorities)


class LtSession:
    """The ``session`` object torrent.TorrentEngine expects, over libtorrent."""

    def __init__(self, listen_port: int, interface: str = "0.0.0.0"):
        import libtorrent as lt
        self.lt = lt
        self.ses = lt.session({
            "listen_interfaces": f"{interface}:{int(listen_port)}",
            "alert_mask": lt.alert.category_t.status_notification
            | lt.alert.category_t.error_notification
            | lt.alert.category_t.tracker_notification
            | lt.alert.category_t.port_mapping_notification,
            "enable_upnp": False, "enable_natpmp": False,
        })

    pex = True

    def apply_settings(self, values: dict) -> None:
        values = dict(values)
        # libtorrent 2 has no session switch for PEX; it is a per-torrent flag
        if "enable_pex" in values:
            self.pex = bool(values.pop("enable_pex"))
        if values:
            self.ses.apply_settings(values)

    def add(self, spec: dict):
        lt = self.lt
        if spec.get("torrent_file"):
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(str(spec["torrent_file"]))
        else:
            params = lt.parse_magnet_uri(spec["magnet"])
        params.save_path = str(spec["save_path"])
        if not self.pex:
            params.flags |= lt.torrent_flags.disable_pex
        h = self.ses.add_torrent(params)
        for i, url in enumerate(spec.get("trackers") or []):
            h.add_tracker({"url": url, "tier": i})
        if spec.get("sequential"):
            h.set_flags(lt.torrent_flags.sequential_download)
        for peer in spec.get("peers") or []:
            host, _, port = peer.rpartition(":")
            h.connect_peer((host, int(port)))
        return LtHandle(h)

    def remove(self, handle) -> None:
        self.ses.remove_torrent(handle.h)

    def pop_alerts(self):
        return self.ses.pop_alerts()

    @property
    def listen_port(self) -> int:
        return self.ses.listen_port()


class LtPortClient:
    """portmap client for one method ("upnp" or "natpmp") through libtorrent."""

    def __init__(self, service: "TorrentService", method: str):
        self.service, self.method = service, method

    def add_mapping(self, port: int, protocol: str, description: str = "") -> int:
        ses = self.service.session
        if ses is None:
            raise RuntimeError("no torrent session")
        key = "enable_upnp" if self.method == "upnp" else "enable_natpmp"
        ses.apply_settings({key: True})
        lt = ses.lt
        proto = lt.portmap_protocol.tcp if protocol == "tcp" else lt.portmap_protocol.udp
        ses.ses.add_port_mapping(proto, int(port), int(port))
        if not self.service.wait_portmap(timeout=6.0):
            raise RuntimeError(f"{self.method}: the router did not confirm the mapping")
        return int(port)

    def remove_mapping(self, port: int, protocol: str) -> None:
        ses = self.service.session
        if ses is not None:
            key = "enable_upnp" if self.method == "upnp" else "enable_natpmp"
            ses.apply_settings({key: False})


class TorrentService:
    def __init__(self, manager):
        self.mgr = manager
        self.settings = manager.settings
        self._lock = threading.RLock()
        self.session: Optional[LtSession] = None
        self.engine: Optional[tor.TorrentEngine] = None
        self.handles: dict[str, LtHandle] = {}       # link id -> handle
        self.seed_started: dict[str, float] = {}
        self.trackers = TrackerManager.from_dict(getattr(self.settings, "trackers", None) or {}) \
            if getattr(self.settings, "trackers", None) else TrackerManager()
        self.portmap_result: Optional[pm.MapResult] = None
        self._portmap_event = threading.Event()
        self._portmap_ok = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- session -----------------------------------------------------------------
    def config(self) -> tor.TorrentSettings:
        return tor.settings_from_dict(getattr(self.settings, "torrent", None) or {})

    def ensure_session(self) -> bool:
        with self._lock:
            if self.session is not None:
                return True
            if not libtorrent_available():
                return False
            port = int(getattr(self.settings, "torrent_listen_port", 6881) or 0)
            try:
                self.session = LtSession(port, getattr(self.settings, "torrent_interface", "0.0.0.0"))
            except Exception as exc:  # port in use: fall back to any free port
                log.warning("torrent port %s unavailable (%s); using a free port", port, exc)
                self.session = LtSession(0, getattr(self.settings, "torrent_interface", "0.0.0.0"))
            self.engine = tor.TorrentEngine(self.session, self.config())
            self._thread = threading.Thread(target=self._loop, name="torrents", daemon=True)
            self._thread.start()
            self.apply_limits()
            if (getattr(self.settings, "portmap", None) or {}).get("enabled"):
                threading.Thread(target=self.map_port, daemon=True, name="portmap").start()
            return True

    def apply_limits(self) -> None:
        if self.session is None:
            return
        flow = self.mgr.flow
        up = int(flow.profiles.active.upload_limit or 0)
        self.session.apply_settings({"download_rate_limit": int(flow.effective_limit_bps()),
                                     "upload_rate_limit": up})
        if self.engine is not None:
            self.engine.settings = self.config()
            self.session.apply_settings(self.engine.settings.session_dict())
        # advanced: raw libtorrent settings_pack keys (e.g. min_reconnect_time)
        advanced = (getattr(self.settings, "torrent", None) or {}).get("advanced") or {}
        if isinstance(advanced, dict) and advanced:
            try:
                self.session.apply_settings({str(k): v for k, v in advanced.items()})
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("ignored advanced torrent settings: %s", exc)

    # -- port mapping --------------------------------------------------------------
    def wait_portmap(self, timeout: float) -> bool:
        self._portmap_event.clear()
        self._portmap_ok = False
        self._portmap_event.wait(timeout)
        return self._portmap_ok

    def map_port(self) -> dict:
        if not self.ensure_session():
            return {"ok": False, "error": NOT_INSTALLED}
        raw = dict(getattr(self.settings, "portmap", None) or {})
        raw.setdefault("enabled", True)
        raw["port"] = self.session.listen_port
        try:
            cfg = pm.config_from_dict(raw)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        clients = {m: LtPortClient(self, m) for m in pm.METHODS}
        self.portmap_result = pm.PortMapper(cfg, clients=clients).map()
        r = self.portmap_result
        return {"ok": r.ok, "method": r.method, "port": r.external_port or r.internal_port,
                "error": "; ".join(r.errors)}

    # -- links -------------------------------------------------------------------------
    def add_link(self, link: Link, magnet: str = "", torrent_file: str = "") -> None:
        if not self.ensure_session():
            link.status = LinkStatus.ERROR
            link.error = NOT_INSTALLED
            return
        save = Path(self.settings.download_dir).expanduser().resolve()
        save.mkdir(parents=True, exist_ok=True)
        own = []
        if magnet:
            m = tor.parse_magnet(magnet)
            own, info_hash, name = list(m.trackers), m.info_hash, m.name
            from urllib.parse import parse_qs, urlsplit
            peers = parse_qs(urlsplit(magnet).query).get("x.pe", [])  # direct peer hints
            if peers:
                link.extra["torrent_peers"] = peers
        else:
            info_hash, name = "", Path(torrent_file).stem
        trackers = self.trackers.for_torrent(own)
        spec = {"magnet": magnet, "torrent_file": torrent_file, "save_path": str(save),
                "trackers": trackers, "sequential": self.config().sequential,
                "peers": list(link.extra.get("torrent_peers") or [])}
        with self._lock:
            handle = self.session.add(spec)
            if magnet:
                self.engine.handles[info_hash] = handle
            else:
                info_hash = str(handle.h.info_hash())
                self.engine.handles[info_hash] = handle
            self.handles[link.id] = handle
        link.plugin = "torrent"
        link.title = link.title or name or info_hash
        link.extra["torrent"] = {"info_hash": info_hash, "magnet": magnet,
                                 "torrent_file": torrent_file, "save_path": str(save)}
        link.status = LinkStatus.RESOLVING

    def handle_for(self, link_id: str) -> Optional[LtHandle]:
        with self._lock:
            return self.handles.get(link_id)

    def pause(self, link_id: str) -> bool:
        h = self.handle_for(link_id)
        if h is None:
            return False
        h.h.unset_flags(self.session.lt.torrent_flags.auto_managed)
        h.h.pause()
        return True

    def resume(self, link_id: str) -> bool:
        h = self.handle_for(link_id)
        if h is None:
            return False
        h.h.resume()
        link = self.mgr.get(link_id)
        for peer in (link.extra.get("torrent_peers") if link else None) or []:
            host, _, port = str(peer).rpartition(":")
            try:  # pausing drops connections; reconnect the magnet's direct peers
                h.h.connect_peer((host.strip("[]"), int(port)))
            except (ValueError, RuntimeError):
                pass
        return True

    def remove(self, link_id: str) -> None:
        with self._lock:
            h = self.handles.pop(link_id, None)
            if h is None:
                return
            for key, val in list(self.engine.handles.items()):
                if val is h:
                    self.engine.handles.pop(key, None)
            self.session.remove(h)
            self.seed_started.pop(link_id, None)

    def files(self, link_id: str) -> list[dict]:
        h = self.handle_for(link_id)
        if h is None:
            return []
        ti = h.h.torrent_file()
        if ti is None:
            return []
        fs = ti.files()
        prios = h.h.get_file_priorities()
        progress = h.h.file_progress()
        pad = self.session.lt.file_storage.flag_pad_file
        return [{"index": i, "path": fs.file_path(i), "size": fs.file_size(i),
                 "done": progress[i] if i < len(progress) else 0,
                 "selected": (prios[i] if i < len(prios) else 4) > 0}
                for i in range(fs.num_files()) if not fs.file_flags(i) & pad]  # hide padding files

    def select_files(self, link_id: str, selected: list[int]) -> list:
        h = self.handle_for(link_id)
        if h is None or h.h.torrent_file() is None:
            raise ValueError("the file list is not known yet")
        fs = h.h.torrent_file().files()
        count = fs.num_files()
        pad = self.session.lt.file_storage.flag_pad_file
        pads = [i for i in range(count) if fs.file_flags(i) & pad]
        info_hash = next(k for k, v in self.engine.handles.items() if v is h)
        # padding files follow the selection so they never block completion
        return self.engine.select_files(info_hash, count, selected=list(selected) + pads)

    # -- polling -----------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                log.debug("torrent tick failed", exc_info=True)

    def tick(self) -> None:
        if self.session is None:
            return
        for alert in self.session.pop_alerts():
            self._on_alert(alert)
        with self._lock:
            items = list(self.handles.items())
        for link_id, h in items:
            link = self.mgr.get(link_id)
            if link is None:
                self.remove(link_id)
                continue
            self._update(link, h)

    def _on_alert(self, alert) -> None:
        name = type(alert).__name__
        lt = self.session.lt
        if name == "tracker_reply_alert":
            self.trackers.record_announce(alert.url, True, peers=int(getattr(alert, "num_peers", 0)), now=time.time())
        elif name == "tracker_error_alert":
            self.trackers.record_announce(alert.url, False, now=time.time())
        elif name == "portmap_alert":
            self._portmap_ok = True
            self._portmap_event.set()
        elif name == "portmap_error_alert":
            self._portmap_ok = False
            self._portmap_event.set()
        del lt

    def _update(self, link: Link, h: LtHandle) -> None:
        st = h.h.status()
        if link.status == LinkStatus.PAUSED:
            return
        meta = st.has_metadata
        link.size = int(st.total_wanted) or link.size
        link.downloaded = int(st.total_wanted_done)
        link.progress = float(st.total_wanted_done) / st.total_wanted if st.total_wanted else 0.0
        link.speed = float(st.download_rate)
        link.eta = ((st.total_wanted - st.total_wanted_done) / st.download_rate) if st.download_rate else None
        link.extra["torrent"].update(peers=int(st.num_peers), seeds=int(st.num_seeds),
                                     upload_rate=int(st.upload_rate), uploaded=int(st.all_time_upload),
                                     ratio=round(st.all_time_upload / st.total_wanted_done, 3)
                                     if st.total_wanted_done else 0.0,
                                     state=str(st.state).split(".")[-1])
        if not meta:
            link.status = LinkStatus.RESOLVING
        elif st.is_seeding or st.is_finished or (st.total_wanted and st.total_wanted_done >= st.total_wanted):
            if link.status != LinkStatus.FINISHED:
                ti = h.h.torrent_file()
                link.filename = link.filename or (ti.name() if ti else st.name)
                link.path = str(Path(link.extra["torrent"]["save_path"]) / (ti.name() if ti else st.name))
                link.status = LinkStatus.FINISHED
                link.finished = time.time()
                link.progress = 1.0
                link.speed = 0.0
                self.seed_started[link.id] = time.time()
                self.mgr._persist()
                self.mgr._broadcast("finished", link)
            self._check_seeding(link, st)
            return
        else:
            if link.status != LinkStatus.DOWNLOADING:
                link.status = LinkStatus.DOWNLOADING
        self.mgr._broadcast("progress", link)

    def _check_seeding(self, link: Link, st) -> None:
        cfg = self.config()
        started = self.seed_started.get(link.id, time.time())
        if cfg.ratio_limit <= 0 and cfg.seed_minutes <= 0:
            stop = True  # no seeding limits set: stop when complete
        else:
            stop = tor.should_stop_seeding(cfg, int(st.all_time_upload), int(st.total_wanted_done),
                                           time.time() - started)
        if stop:
            link.extra["torrent"]["seeding"] = "done"
            self.remove(link.id)
            self.mgr._persist()
            self.mgr._broadcast("grabbed", link)
        else:
            link.extra["torrent"]["seeding"] = "seeding"

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            if self.session is not None:
                for h in list(self.handles.values()):
                    try:
                        h.h.pause()
                    except Exception:
                        pass
            self.trackers_save()

    def trackers_save(self) -> None:
        self.settings.trackers = self.trackers.to_dict()
