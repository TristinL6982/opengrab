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
    per_link_chunks: int = 2           # parallel range-chunks per link (1 = single)
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
    timeout_s: float = 30.0
    ua_rotation: bool = False
    ua_pool: list[str] = Field(default_factory=list)
    media_max_height: int = 0          # 0 = best progressive format
    media_playlist_max_entries: int = 10000

    # captcha solving (unpaywalled: BYO keys, local OCR, or manual)
    captcha_provider: str = "auto"     # auto|manual|2captcha|capmonster|capsolver|nopecha|ddddocr|tesseract
    captcha_2captcha_key: str = ""
    captcha_capmonster_key: str = ""
    captcha_capsolver_key: str = ""
    captcha_nopecha_key: str = ""
    captcha_tesseract_enabled: bool = False   # opt-in second local OCR
    captcha_tesseract_path: str = ""          # empty = auto-detect
    captcha_audio_enabled: bool = False      # opt-in offline audio captcha solver
    captcha_vosk_model_path: str = ""         # folder of an unpacked Vosk model
    captcha_ffmpeg_path: str = ""             # empty = ffmpeg on PATH
    captcha_browser_enabled: bool = False   # opt-in real-browser Cloudflare solver
    captcha_browser_engine: str = "edge"    # edge (CDP) | camoufox
    captcha_manual_timeout_s: int = 120
    captcha_auto_failover: bool = True # try cheaper/faster solvers first, escalate

    # grabbing
    auto_start_after_grab: bool = True
    stop_at_paywall: bool = True       # mark paywalled instead of erroring
    grab_timeout_s: float = 45.0

    # ui
    theme: str = "dark"
    poll_ms: int = 1000

    # local api server
    api_host: str = "127.0.0.1"
    api_port: int = 5678
    api_token: str = ""
    base_path: str = ""             # reverse-proxy path prefix, e.g. /opengrab
    terminal_enabled: bool = False  # shell API is opt-in and always token-gated
    clipboard_monitor_enabled: bool = False

    # Password search remains opt-in and uses only a user-selected local file.
    auto_extract_archives: bool = True
    archive_max_files: int = 10000
    archive_max_expanded_gib: int = 16
    archive_password_file: str = ""
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
            if os.environ.get("OPENGRAB_DOWNLOAD_DIR"):
                settings.download_dir = os.environ["OPENGRAB_DOWNLOAD_DIR"]
            return settings
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
    return s
