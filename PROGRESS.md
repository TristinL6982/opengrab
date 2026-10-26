# OpenGrab — build state (pause point)

> **Status note:** This file is an older handoff and its DONE/NOT YET WRITTEN
> lists are stale. The engine, API server, and UI files now exist in the
> repository, but their existence does not mean they have been fully verified.
> Inspect the current tree and Git status before relying on this snapshot.
> `PROJECT_SPEC.md` is the authoritative product requirements document;
> `AGENTS.md` contains instructions for coding agents.

**Date paused:** 2026-10-25. **Location:** `C:\Users\trist\opengrab`
Git repo initialized at this pause point — check `git log` for the commit.

## Open decisions — RESOLVED 2026-10-25 (see PROJECT_STATUS.md §2 for full text)
1. Platforms: Windows/Linux/macOS via portable Python core; no Java rewrite.
2. Auth: v1 single owner token; remote access requires auth.
3. Tray + LinkGrabber clipboard capture: yes; monitoring must be visible + user-controllable.
4. Docker: supported target; unbuildable/unverifiable on this machine (no docker CLI) — document as unverified.
5. HTTPS: local-only default, HTTPS off on localhost; remote requires HTTPS (trusted proxy or explicit TLS); never unauthenticated HTTP.
6. Default port: 5678 (JD2 parity), configurable. (Code currently 8765 — change pending.)

## What OpenGrab is
A modern, unpaywalled JDownloader-2-style grabber/downloader for Windows.
Python 3.12 backend + web UI (served on localhost, rendered in a native
WebView2 window) → bundled to `OpenGrab.exe` with PyInstaller. Also ships a
Docker image (feature parity requested vs the JD2 docker docs).

## Verified environment facts
- Windows 11, git-bash terminal (POSIX syntax; native tools need `C:/` paths).
- Python: `python3` = 3.12.4 (USE THIS), `python` = 3.14.9 (do NOT use for venv/wheels).
- Venv: `C:\Users\trist\opengrab\.venv` — ALL requirements installed & verified:
  httpx[http2], curl_cffi 0.16.3, pywebview 6.2.1 (pulled bottle+pythonnet),
  pyinstaller 6.22.3, bs4, lxml, aiofiles, pydantic 2.14, rich, yt-dlp 2026.10.21,
  ddddocr 1.6.1 (onnxruntime+opencv+Pillow+numpy), pywin32-ctypes.
- Network quirk: DNS is filtered — hetzner/`speed.hetzner.de` do NOT resolve,
  but pypi.org, files.pythonhosted.org, example.com DO. Smoke test target:
  `https://files.pythonhosted.org/packages/b3/38/89ba8ad64ae25be8de66a6d463314cf1eb366222074cfda9ee839c56a4b4/mdurl-0.1.2-py3-none-any.whl`
  (9979 bytes; verified: HEAD 200 accept-ranges:bytes, ranged GET 206).
- curl_cffi API verified: `Session(impersonate="chrome")`,
  `s.get(url, stream=True)`, `resp.iter_content(chunk_size=)`, `.status_code`,
  `.headers` (dict-able), `.content`, `.url`. (probe file: tests/probe_curl.py)

## DONE (written, not yet executed/tested)
- `requirements.txt` (all installed)
- `src/opengrab/__init__.py` — app metadata (name OpenGrab, v0.1.0)
- `src/opengrab/models.py` — Link/Package/Account/FilterRule/PluginInfo + LinkStatus enum
- `src/opengrab/settings.py` — pydantic Settings, JSON persist to %APPDATA%/OpenGrab
- `src/opengrab/http_client.py` — anti-bot client (curl_cffi impersonation, UA
  rotation, extra headers, proxy, per-domain cookie jars, httpx fallback)
- `src/opengrab/filters.py` — FilterManager (regex/glob include-exclude, persisted)
- `src/opengrab/accounts.py` — AccountManager (b64-obfuscated local creds, find/for_domain)
- `src/opengrab/captcha/base.py` — CaptchaRequest/Result, ManualSolver,
  DdddOCRSolver, TwoCaptchaSolver, CapMonsterSolver, CapSolverSolver
- `src/opengrab/captcha/chain.py` — SolverChain with priority + failover, 'auto' order
- `src/opengrab/plugins/base.py` — SitePlugin/BridgePlugin, GrabResult/ResolveResult,
  PluginContext (solve_captcha helper, extract_captcha, find_direct_link, find_magnets,
  account_for), exceptions CaptchaRequired/LoginRequired/PaywallRequired
- `src/opengrab/plugins/registry.py` — PluginRegistry: builtin package scan +
  user-dir hot-reload, for_url() with specificity sort
- `src/opengrab/plugins/builtin/direct.py` — DirectLinkPlugin (HEAD/GET sniff, CD filename)
- `src/opengrab/plugins/builtin/generic.py` — GenericPagePlugin (og:title, <a>/magnet/
  <video>/embedded-JSON link extraction, file-content sniff)
- `src/opengrab/plugins/builtin/ytbridge.py` — yt-dlp bridge (1000+ sites, best-format
  picker, playlist children). NOTE: `_supported_domains()` has a dead loop
  (`ie._match_valid_url.__code__ and []` hack) — clean it up on resume.

## NOT YET WRITTEN (resume order)
1. `src/opengrab/plugins/__init__.py` (export registry+base)
2. `src/opengrab/engine/__init__.py`, `engine/downloader.py` — resumable .part
   download, N parallel range chunks (verified: 206 + content-range work),
   speed-limit token bucket, progress/ETA cb, stop event.
3. `src/opengrab/engine/manager.py` — DownloadManager: queue state machine
   (WAITING→RESOLVING→[CAPTCHA/LOGIN/PAYWALL]→DOWNLOADING→FINISHED/ERROR),
   add_urls (parse/filter/grab/children), worker thread (max_parallel),
   retry/backoff, provide_captcha/provide_login re-resolve, speed/eta smoothing,
   state persistence (state.json) for restart, notifications list.
4. `src/opengrab/server.py` — ThreadingHTTPServer: static UI + JSON API
   (state, urls, start/pause/resume, remove, move, reorder, captcha, login,
   paywall, settings, filters, accounts, plugins, health, captcha-test),
   file manager (list/mkdir/delete/rename/download/upload), terminal (exec+poll),
   clipboard get/set, audio streaming (Range), web auth (pbkdf2 hash + token
   cookie), optional HTTPS (self-signed or user cert), reverse-proxy headers
   (X-Forwarded-For/Proto), base_path support, PWA files (manifest, sw.js, icons).
5. `src/opengrab/app.py` — bootstrap: settings→accounts→filters→registry→
   solver chain→manager→server; GUI mode: pywebview window (WebView2) +
   pystray tray (needs `pip install pystray` — NOT yet in requirements!) with
   Click'n'Load clipboard watcher thread (win32 via ctypes, poll 2s);
   headless mode for Docker.
6. `src/opengrab/cli.py` + `__main__.py` — flags: --gui (default), --headless,
   --port (default 5678 = JD2 parity), --bind, --data-dir, --grab <url>,
   --list-plugins, --smoke-test, --version. Env var overrides (OPENGRAB_* +
   UGID/PUID/PGID for docker, TZ).
7. `src/opengrab/smoke.py` — headless self-test: settings tmp dir, registry
   scan, filters, captcha chain status, add mdurl whl URL (above), start,
   poll until FINISHED, verify size 9979 + sha256, ranged resume check,
   API round-trip via urllib to 127.0.0.1:port. Exit 0/1.
8. `src/opengrab/ui/` — index.html, style.css (dark, modern), app.js (tabs:
   Queue/Active/Finished, Captcha modal w/ image+input, Login modal, Accounts,
   Filters, Plugins, Settings, File manager, Terminal, Notifications, Clipboard
   sync, Audio player, drag-drop URLs, speed graph canvas), manifest.json,
   sw.js, icons (generate PNGs with Pillow).
9. Build: `build/OpenGrab.spec` (datas: ui/*, ddddocr onnx models, certifi;
   hiddenimports: yt_dlp, ddddocr, curl_cffi; console=False for GUI; add
   pystray), `build/build.bat`, one-click. Also `build/build_docker.bat` optional.
10. Docker: `Dockerfile` (python:3.12-slim, UGID handling, EXPOSE 5678,
    VOLUME /config, HEALTHCHECK /api/health), `docker-compose.yml`,
    `.env.example` (all OPENGRAB_* + UGID + TZ), version tag scheme (0.1.0
    / 2026-10-25), Synology + unRAID install notes.
11. `README.md` — mirror the JD2 docker doc sections the user listed:
    env vars, deployment, volumes, ports, changing params, compose file,
    image versioning/update, Synology, unRAID, U/GIDs, shell access, GUI
    access, security (SSVNC→n/a for exe, note; certs; web auth; token),
    reverse proxy (hostname + URL path), web control panel, clipboard sync,
    web audio, file manager, web notifications, web terminal, PWA, GPU
    accel (n/a — no transcoding; document), remote access (web auth +
    tailscale note = MyJDownloader parity), direct connection, Click'n'Load,
    troubleshooting (won't start: WebView2 runtime, port, firewall, smoke
    test), support.

## Feature-parity checklist (user requirement: "match and exceed")
JD2 docker docs sections → our implementation (see README numbering above):
env vars ✓(planned), volumes ✓, ports ✓(5678), runtime param changes
(= /api/settings + env) ✓, compose ✓, versioning/tags ✓, updates ✓,
Synology ✓docs, unRAID ✓docs, UGID ✓, shell access ✓docs, GUI access ✓
(exe GUI / browser for docker), SSVNC (n/a, document), certificates ✓,
VNC password (n/a, document), web auth users ✓, reverse proxy hostname ✓
(host header + docs), reverse proxy URL path ✓ (base_path), web control
panel ✓ (UI), host clipboard sync ✓ (exe: pywebview+ctypes; docker: clip/pb),
web audio ✓, web file manager ✓, web notifications ✓, web terminal ✓,
PWA ✓, GPU accel (n/a, document), MyJDownloader remote access ✓ (web auth
+ token from any device), direct connection ✓ (same), Click'n'Load ✓
(tray+clipboard watcher), troubleshooting ✓ (+ --smoke-test).

## Gotchas / decisions
- Use `python3` (3.12) everywhere; venv Scripts python for all commands.
- PyInstaller onedir build (fast start); onefile optional in docs.
- No repo clones needed; captcha providers are HTTP APIs.
- ddddocr import is heavy (~2-5s) → keep lazy import in DdddOCRSolver (done).
- ytbridge `_supported_domains()` cache built once; first call slow.
- `cgi` module deprecated in 3.12 → upload API uses raw octet-stream body,
  not multipart.
- Web auth default: disabled for local convenience; enabled in Docker docs
  (auto-generated password via OPENGRAB_PASSWORD like JD2 does).
- User profile note: external targets only for probing; local ports are fine
  to run/serve (we serve our own app on 127.0.0.1).
- After resume: run `.venv/Scripts/python.exe -m opengrab --smoke-test`
  (with PYTHONPATH=src or install -e) to shake out import/logic bugs BEFORE
  PyInstaller.
