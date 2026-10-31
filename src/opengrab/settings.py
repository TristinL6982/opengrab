"""Settings: pydantic-backed, persisted as JSON in the user app-data dir."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, PrivateAttr, ConfigDict

from . import APP_ID, APP_NAME


def app_data_dir() -> Path:
    """Per-user data dir:  %APPDATA%/OpenGrab   (settings.json, accounts, state)"""
    override = os.environ.get("OPENGRAB_DATA_DIR")
    if override:
        p = Path(override).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    p = base / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_plugins_dir() -> Path:
    p = app_data_dir() / "plugins"
    p.mkdir(parents=True, exist_ok=True)
    return p


class Settings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    
    # downloads
    download_dir: str = str(Path.home() / "Downloads")
    max_parallel: int = 3              # concurrent links
    per_link_chunks: int = 2           # connections per download (1-16); W6 splits the slowest range when one frees up
    segment_min_mb: int = 1            # never split a range below this size
    speed_limit_kbps: int = 0          # 0 = unlimited
    retry_max: int = 3
    chunk_size_kb: int = 256
    keep_partials: bool = True
    verify_hash: bool = False

    # anti-bot / network
    impersonate: str = "chrome"        # chrome|firefox|safari|edge|chrome124|... (curl_cffi)
    user_agent: str = ""               # override UA (empty = use impersonation default)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    proxy: str = ""                    # http://user:pass@host:port  (empty = none)
    proxies: list[str] = Field(default_factory=list)        # W7: tried in order, with failover
    proxy_rules: dict[str, str] = Field(default_factory=dict)  # host pattern -> proxy URL or "direct"
    proxy_bypass: list[str] = Field(default_factory=list)   # host patterns that never use a proxy
    proxy_use_system: bool = False                           # also use the OS/Windows proxy
    site_credentials: list[dict] = Field(default_factory=list)  # W7 server/proxy logins (DPAPI)
    timeout_s: float = 30.0
    ua_rotation: bool = False
    ua_pool: list[str] = Field(default_factory=list)
    media_max_height: int = 0          # 0 = best progressive format
    media_preset: str = ""            # "" = old behaviour; else a media.presets name (best, 1080, audio-m4a, ...)
    media_playlist_max_entries: int = 10000
    media_ffmpeg_path: str = ""        # empty = ffmpeg on PATH; used to merge video+audio

    # captcha solving (unpaywalled: BYO keys, local OCR, or manual)
    captcha_provider: str = "auto"     # auto|manual|2captcha|capmonster|capsolver|nopecha|ddddocr|tesseract
    captcha_2captcha_key: str = ""
    captcha_capmonster_key: str = ""
    captcha_capsolver_key: str = ""
    captcha_nopecha_key: str = ""
    captcha_ocr_enabled: bool = False          # opt-in local OCR add-on (ddddocr)
    captcha_ocr_ignore_hardware: bool = False  # run local OCR even on weak hardware
    captcha_tesseract_enabled: bool = False   # opt-in second local OCR
    captcha_tesseract_path: str = ""          # empty = auto-detect
    captcha_audio_enabled: bool = False      # opt-in offline audio captcha solver
    captcha_vosk_model_path: str = ""         # folder of an unpacked Vosk model
    captcha_ffmpeg_path: str = ""             # empty = ffmpeg on PATH
    captcha_browser_enabled: bool = False   # opt-in real-browser Cloudflare solver
    captcha_browser_engine: str = "edge"    # edge (CDP) | camoufox
    captcha_manual_timeout_s: int = 120
    captcha_inbox_max_pending: int = 50       # W2: pending challenges kept for a human
    captcha_auto_failover: bool = True # try cheaper/faster solvers first, escalate

    # grabbing
    auto_start_after_grab: bool = True
    stop_at_paywall: bool = True       # mark paywalled instead of erroring
    grab_timeout_s: float = 45.0

    # ui
    theme: str = "dark"                 # legacy, unused; see ui_theme
    # M14a appearance
    ui_theme: str = "classic"           # classic (light) | classic-dark | system
    ui_accent: str = ""                 # CSS colour, "" = theme default
    ui_density: str = "normal"          # compact | normal | comfortable
    ui_hidden_columns: list[str] = Field(default_factory=list)  # size status eta speed progress
    ui_toolbar_labels: bool = True
    ui_language: str = "auto"           # auto (follow Windows) | en | zh-CN
    onboarding_done: bool = False
    dashboard_tiles: list[str] = Field(default_factory=lambda: ["today", "speed", "active", "days", "sites"])
    # remote access (M12b policy; restart to apply)
    allow_remote_http: bool = False     # plain HTTP from the LAN (Docker); token still required
    trusted_proxies: list[str] = Field(default_factory=list)  # reverse proxies that set X-Forwarded-*
    poll_ms: int = 1000

    # local api server
    api_host: str = "127.0.0.1"
    api_port: int = 5678
    api_token: str = ""
    base_path: str = ""             # reverse-proxy path prefix, e.g. /opengrab
    terminal_enabled: bool = False  # shell API is opt-in and always token-gated
    clipboard_monitor_enabled: bool = False
    premium_prefer_debrid: bool = True  # W9: try a debrid account before the hoster plugin
    # W8 LinkGrabber v2
    linkgrabber_online_check: bool = True
    spider_max_pages: int = 100
    spider_delay_s: float = 0.5
    spider_respect_robots: bool = True
    cnl_enabled: bool = True         # Click'n'Load listener on 127.0.0.1
    cnl_port: int = 9666
    toasts_enabled: bool = True     # desktop toast for finished/failed/captcha (W1)

    # W3-W5 flow control (engine/flow.py); empty values mean defaults
    scheduler_enabled: bool = False
    scheduler_windows: list[dict] = Field(default_factory=list)  # {start, end, days, action, speed_kbps}
    max_per_host: int = 0                                       # 0 = unlimited
    per_host_limits: dict[str, int] = Field(default_factory=dict)
    categories_enabled: bool = False                            # Video/Music/... folders
    categories: list[dict] = Field(default_factory=list)        # empty = built-in categories
    speed_profiles: dict = Field(default_factory=dict)          # ProfileSet.to_dict()
    data_cap: dict = Field(default_factory=dict)                # {enabled, limit_mb, period, action, slow_kbps}
    queues: dict = Field(default_factory=dict)                  # QueueManager.to_dict()
    retry_policy: dict = Field(default_factory=dict)            # {base_delay, factor, max_delay}
    hash_checksum_files: bool = False                           # look for .sha256/.md5 next to downloads

    # Password search remains opt-in and uses only a user-selected local file.
    auto_extract_archives: bool = True
    archive_max_files: int = 10000
    archive_max_expanded_gib: int = 16
    archive_password_file: str = ""
    archive_7zip_path: str = ""        # W10: 7-Zip for split sets and RAR (blank = find it)
    virustotal: dict = Field(default_factory=dict)  # {enabled, api_key, upload_unknown, min_detections}
    avscan: dict = Field(default_factory=dict)  # W10: {enabled, scanner, custom_command, infected_codes, timeout_s, quarantine}
    # W11 BitTorrent (libtorrent add-on) and router reconnect
    torrent: dict = Field(default_factory=dict)       # {dht, pex, lsd, sequential, ratio_limit, seed_minutes}
    torrent_listen_port: int = 6881
    torrent_interface: str = "0.0.0.0"
    torrent_open_files: bool = True                   # open downloaded .torrent files
    trackers: dict = Field(default_factory=dict)      # TrackerManager.to_dict()
    tracker_list_url: str = ""                         # public tracker list merged daily
    portmap: dict = Field(default_factory=dict)       # {enabled, protocols, methods, manual}
    reconnect: dict = Field(default_factory=dict)     # {enabled, command, ip_check_url, ...}
    # M13 automation
    event_hooks: list[dict] = Field(default_factory=list)   # {event, command, enabled}
    hooks_enabled: bool = True
    webhooks: list[dict] = Field(default_factory=list)      # {url, events, secret, enabled}
    folder_watch: list[dict] = Field(default_factory=list)  # {path, mode: review|queue, enabled}
    anti_standby: str = "downloading"                       # off | downloading | always
    when_done: str = "nothing"                              # nothing | exit | sleep | hibernate | shutdown
    when_done_delay_s: int = 60
    when_done_once: bool = True                             # reset to nothing after it ran
    # W12 plugins
    plugin_sandbox_enabled: bool = True
    plugin_index_url: str = ""            # template update index
    plugin_auto_update: bool = False
    marketplace_url: str = ""
    marketplace_require_signature: bool = True
    marketplace_keys: list[str] = Field(default_factory=list)  # trusted Ed25519 public keys
    packagizer_rules: list[dict] = Field(default_factory=list)

    # maintenance
    confirm_delete: bool = True

    def save(self) -> None:
        with self._lock:
            p = app_data_dir() / "settings.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(p)

    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)


def load_settings() -> Settings:
    p = app_data_dir() / "settings.json"
    if p.exists():
        try:
            settings = Settings.model_validate(json.loads(p.read_text(encoding="utf-8")))
            return _apply_env(settings)
        except Exception:
            # Backup the corrupt file before falling back to defaults
            from datetime import datetime
            backup_name = p.with_name(f"settings.json.bad-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            try:
                shutil.copy2(p, backup_name)
                print(f"Warning: corrupt settings.json backed up as {backup_name.name}", file=sys.stderr)
            except Exception:
                pass  # Silently ignore if backup fails
    s = Settings(download_dir=os.environ.get(
        "OPENGRAB_DOWNLOAD_DIR", str(Path.home() / "Downloads")))
    s.save()
    return _apply_env(s)


def _apply_env(settings: "Settings") -> "Settings":
    """Environment overrides for Docker and services (not saved)."""
    env = os.environ
    if env.get("OPENGRAB_DOWNLOAD_DIR"):
        settings.download_dir = env["OPENGRAB_DOWNLOAD_DIR"]
    if env.get("OPENGRAB_ALLOW_REMOTE_HTTP") is not None:
        settings.allow_remote_http = env["OPENGRAB_ALLOW_REMOTE_HTTP"].strip().lower() in ("1", "true", "yes", "on")
    if env.get("OPENGRAB_TRUSTED_PROXIES"):
        settings.trusted_proxies = [p.strip() for p in env["OPENGRAB_TRUSTED_PROXIES"].split(",") if p.strip()]
    return settings
