# OpenGrab Product Specification
## 1. Vision
free open-source download manager that matches and exceeds JDownloader 2; native desktop app launched from OpenGrab.exe, Windows first; web UI/remote API is secondary; clean modern UI; best captcha and Cloudflare handling

## 2. HARD CONSTRAINT: runs on about 99% of machines
- **Baseline machine:** 2014-era laptop: dual-core x64 CPU, 4 GB RAM, no usable GPU, Windows 10 x64. The app must install, launch and download normally on it.
- **Base app is light:** modest idle RAM (target under 150 MB for the app process, excluding WebView2), and the installer and exe contain **no ML dependencies** (no torch, tensorflow, onnxruntime, CUDA or bundled models).
- **Local AI captcha solving is optional and OFF by default.** Enabled only by an explicit user opt-in in settings.
- **Tiny CPU-only models:** ONNX run with `onnxruntime` CPU, ideally under 30 MB of models in total; `onnxruntime` and the models are downloaded only on opt-in, never shipped in the installer.
- **No GPU or CUDA dependency** anywhere in the app. GPU acceleration may never be required.
- **Bounded resources:** local AI peak RAM under about 300 MB; models load lazily on the first captcha and unload when idle; inference runs off the UI thread with a timeout.
- **Weak-hardware fallback:** the app detects weak hardware (low RAM, few cores, slow test inference) and skips local AI, falling back to the real-browser solver, then configured solver services, then the manual captcha inbox.
- **Real-browser solver uses the system browser:** drive the installed Edge or Chrome through CDP (Edge ships with Windows 10/11) instead of bundling Chromium where possible; bundled or downloaded Chromium is an optional last resort.
- **Acceptance:** each milestone that touches captcha, browser or packaging must state its RAM, disk and dependency impact, and the default install must pass `--smoke-test` without any ML package present.

## 3. Architecture
Python core src/opengrab; pywebview native window loading the bundled local UI plus tray; PyInstaller one-folder dist/OpenGrab/OpenGrab.exe; local API 127.0.0.1:5678 with a token on EVERY request, no CORS, Host/Origin checks

## 4. Critical bugs to fix first
- C1. Stopping a multi-chunk download marks it FINISHED with a corrupt file  
- C2. Resuming a multi-chunk download after app exit "finishes" instantly with a corrupt file  
- C3. A crash or kill during a download deletes the link from the queue  
- C4. Any website the user visits can get remote code execution through the local API  
- C5. One invalid setting silently wipes all settings on next start  
- C6. "Remove this link?" deletes the downloaded file from disk  
- C7. Desktop launch can show another app, and the port silently drifts  
- C8. Pausing a download blocks the queue  
- B18 run_opengrab.bat wrong Python  
- B19 build.bat uses py -3.12  
- B20 pytest collects 0 tests  

## 5. Milestones M2-M14
| Milestone | Name | Scope | Parity rows | New features |
|---|---|---|---|---|
| M2 | Build and test hygiene | Pytest-collectable tests, failing regression tests from `repro/`, fix `build.bat`/`run_opengrab.bat`, Windows CI that builds the exe and runs `--smoke-test`. | 3 | 0 |
| M3 | Download integrity | Chunk-map sidecar, no finalise on cancel, crash-safe queue, pause frees the slot, bounded retries, safe filenames, live `max_parallel`. | 8 | 2 |
| M4 | Local API security and settings safety | Token on every request, no CORS, Host/Origin checks, no token in settings, validated settings, remove keeps files, account delete, DPAPI. | 6 | 0 |
| M5 | Reliable desktop launch | Single instance, exclusive port bind, nonce check, no console, icon, logs, WebView2 check, tray, smaller bundle. | 4 | 1 |
| M6 | Cloudflare and captcha | Challenge detection, real-browser solver, cf_clearance handoff, widget harvest, captcha inbox, fixed solver chain. | 11 | 3 |
| M7 | Plugin system | Declarative templates, first-wave hosts and crawlers, offline registry, plugin updates, health dashboard, SDK. | 7 | 3 |
| M8 | LinkGrabber v2 | Packages, online check, smart paste, deep crawl fixes, Click'n'Load, containers, dedupe. | 9 | 3 |
| M9 | Media | yt-dlp with ffmpeg merge, presets, HLS/DASH, gallery-dl. | 2 | 2 |
| M10 | Accounts, premium and debrid | Premium plugin paths, debrid services, usage rules, cookie login. | 5 | 1 |
| M11 | Queue, scheduler and network | Scheduler, per-host limits, proxies, reconnect, hash verify, extraction extras, notifications, torrent engine. | 16 | 6 |
| M12 | Remote and browser | Pairing, secure remote/mobile UI, browser extension, Docker. | 6 | 2 |
| M13 | Automation | Event hooks, Folder Watch, Anti-Standby, shutdown, CLI, webhooks. | 4 | 2 |
| M14 | Polish and distribution | Updates, onboarding, i18n, themes, accessibility, advanced settings, JD2 import, other platforms. | 12 | 5 |

These replace all older milestone labels in PROGRESS.md. REVIEW.md section 5 calls M2-M5 'M1-M4'.
For M2: Convert `tests/*.py` into pytest tests, keeping the scripts runnable. Add `pyproject.toml` with `[tool.pytest.ini_options] testpaths=["tests"]`. Rewrite `build/build.bat` to use `..\.venv\Scripts\python.exe -m PyInstaller`. Delete `run_opengrab.bat`, or rewrite it to call `.venv\Scripts\OpenGrab` or the built exe. Add a GitHub Actions workflow on `windows-latest` that runs pytest, builds the exe, and runs `OpenGrab.exe --smoke-test`.
For M3: Persist per-chunk progress in a sidecar (`<file>.part.json` holding chunk ranges and bytes written, fsync'd periodically). On resume, read it and resume each chunk. Never finalize unless every chunk is complete and the size matches. When a chunk is cancelled, raise cancel out of `_parallel`, never finalize. Persist DOWNLOADING links (as PAUSED on load) and keep the `_load_links` recovery path. Pause must release the worker and the connection (stop the stream, keep the sidecar, requeue on resume). Bound `_stream` retries by `retry_max`. Cancel sibling chunks on fatal error. Sanitize the Content-Disposition name and honor RFC 5987 `filename*`. Apply `max_parallel` live.
For M4: Remove `Access-Control-Allow-Origin: *`; send no CORS by default. Require the token on **every** `/api/*` request, including loopback. Hand the token to the pywebview window through `js_api` or an HttpOnly SameSite=Strict cookie set on first load with a per-launch nonce. Validate `Host` against `127.0.0.1` / `localhost` / configured names. Reject requests whose `Origin` is not same-origin. Stop returning `api_token` from `GET /api/settings`. Turn on `validate_assignment=True` in Settings and whitelist which keys `POST /api/settings` may change. On a bad `settings.json`, back it up and **do not** overwrite it. `terminal_enabled` may only be changed from the desktop UI (or remove the terminal). Remove-link must not delete files unless the user explicitly chooses "Remove and delete file". Fix account delete to use the id. Store credentials with Windows DPAPI or `keyring`.
For M5: Single-instance lock: a named mutex on Windows; a second launch focuses the existing window or sends it `--grab` URLs. On Windows, bind with `SO_EXCLUSIVEADDRUSE` (override `server_bind`) and do not set SO_REUSEADDR. If 5678 is taken, show a clear dialog, or pick a free port **without persisting it**. Never write `api_port` from a CLI `--port` override (remove `settings.save()` at `app.py:42-43`). Before `create_window`, GET `/api/ping` and require a per-launch nonce to match. Otherwise abort with an error. `console=False` plus an `.ico`; write a log file under `%APPDATA%\OpenGrab\logs`. Detect a missing WebView2 runtime and show a message. Lazy or optional ddddocr to shrink the bundle.

## 6. Feature parity
The full JD2 comparison is in docs/plan/FEATURES.md, plugin inventory docs/plan/JD2_PLUGIN_INVENTORY.md

### 6a. Required in the final release (IDM-class features, requested by Tristin 2026-10-27)
- **Speed acceleration (M6/M11):** dynamic segmentation (split the remaining range of the slowest segment when a connection frees up, IDM-style), up to 8-16 connections per file when the server allows ranges, HTTP keep-alive and connection reuse per host, and a fallback to one connection when ranges aren't supported. Target: close to line speed on range-capable servers (IDM advertises 5-8x; measure against single-connection curl in a benchmark test).
- **Resume and recovery (M3, done, plus M11):** resume after network loss, app crash, PC shutdown or power cut from the chunk-map sidecar. Auto-retry with backoff when the network returns, re-validate with ETag/Last-Modified, refresh expired links through the hoster plugin, and repair (re-download only bad chunks) when a hash check fails.
- **Browser integration (M12):** a Manifest V3 extension for Chrome, Edge, Opera and Brave, plus a Firefox build. It intercepts downloads (by file type and size rules, with a hold-Alt bypass), adds a right-click "Download with OpenGrab" entry, and passes cookies and referrer. It talks to the app over the token-authenticated local API or native messaging.
- **Video grabbing (M9 + M12):** the extension detects media and HLS/DASH streams on the current page and shows a "Download this video" button. The app grabs them with yt-dlp/ffmpeg with quality selection.
- **Scheduling and queuing (M11):** a scheduler (start/stop times, days, speed limit windows, shut down/sleep when done), multiple named queues with their own concurrency, and categories (Video, Music, Documents, Archives, Programs) with per-category folders and file-type rules.
- **Antivirus scan on completion (M11):** optional, off by default. Use Windows Defender via MpCmdRun.exe -Scan -ScanType 3 -File <path> when it's present, or a user-configured scanner command line (e.g. Avast, ESET). Show the result in the UI, quarantine or flag files that fail, and never block the queue.
All of these must respect the hard constraint in section 2 (weak 2014 laptops, no GPU, low RAM).

### 6b. Also required in the final release (Tristin, 2026-10-27 8:12 PM)
- **Browser integration module (M12):** the OpenGrab extension (see 6a) ships prebuilt in the installer and store listings, and can also be built locally from `extension/` (unpacked/dev mode). Supports all popular browsers: Chrome, Edge, Firefox, Opera, Brave, Vivaldi. Advanced integration: per-site enable/disable, file-type and minimum-size rules, a key to bypass, catching downloads started by page scripts, and passing cookies, referrer and user agent.
- **Download All (M12):** extension action and LinkGrabber command to grab every link or media file on the current page, filtered by type and pattern, into the LinkGrabber for review.
- **Website spider and grabber (M8):** crawl a site from a start URL with depth limit, same-domain or include/exclude patterns, file-type filters, robots.txt respect, and a request-rate limit; results go to the LinkGrabber.
- **Dark theme and customizable interface (M14, dark exists):** dark, light and follow-system themes plus accent colour; configurable columns, sort, toolbar buttons, and a compact or comfortable layout, all saved per user.
- **Drag and drop (works; keep):** drop links, text, .txt/.dlc/.crawljob files onto the window and the tray icon; drag to reorder the queue.
- **Download categories (M11, see 6a).**
- **Multilingual (M14):** all UI strings in translation files, language picker, follow the OS language; ship at least English plus community translations.
- **Quick update (M14):** one-click in-app update check and install from signed GitHub Releases (small delta updates when possible), with rollback; plugins update separately.
- **Download limits (M11):** global and per-download speed limits (exists), scheduled speed windows, and data caps (stop or slow after N GB per day/month).
- **Proxy servers (M11):** HTTP, HTTPS, SOCKS4/4a, SOCKS5 (with remote DNS), system proxy, PAC/WPAD auto-config, proxy lists with per-host rules and failover.
- **Authentication protocols (M11):** Basic, Digest, Negotiate, NTLM and Kerberos for servers and proxies (via libcurl/curl_cffi auth options with Windows SSPI for integrated login), with saved per-site credentials stored with DPAPI.
- **Automatic antivirus checking (M11, see 6a)** and **simple installation wizard (M14, see section 5 M14 installer requirements).**

### 6c. Also required in the final release (Tristin, 2026-10-27 8:17 PM; Motrix-class features)
- **BitTorrent (M11):** libtorrent-based engine (CPU-light, no GPU) with magnet links, per-file selection inside a torrent, sequential download option, seeding ratio/time limits, DHT/PEX/LSD.
- **Tracker management (M11):** built-in tracker list with automatic updates from public lists, per-torrent add/remove, and health checks (announce success, seeds/peers).
- **Port mapping (M11):** UPnP and NAT-PMP for the torrent listen port, with a manual port option and a reachability test.
- **Upload and download limits with speed profiles (M11):** global and per-task upload/download limits; named profiles (e.g. Normal, Gaming, Night) switchable from the tray and the scheduler.
- **SQLite-backed sessions (M6):** move queue and task state to SQLite (WAL mode) so every download, torrent and LinkGrabber entry is restored after a restart or crash; keep the chunk-map sidecars for byte-level resume.
- **Customizable dashboard (M14):** transfer stats (speed graph, totals per day), live activity, and rearrangeable task tiles.
- **Notifications (M11):** native Windows toast notifications when downloads finish or fail, plus an in-app notification center with history.
- **Plugin sandboxing and marketplace (M7):** declarative plugins plus script plugins run in a sandbox (QuickJS via a small CPU-only runtime, or restricted subprocesses) with fine-grained permissions (network hosts, file access, cookies) the user approves; an in-app plugin marketplace with signed plugins, ratings and updates.
- **Browser extension one-click handoff (M12, see 6a/6b):** Chrome, Edge and Firefox (plus others) hand browser downloads to OpenGrab in one click.
- **Official CLI (M13):** `opengrab` command-line client for everyday shell use and AI agents (add, list, pause, resume, remove, status, JSON output), talking to the token-authenticated local API.
- **Headless server and secure pairing (M12):** Docker-ready headless server with device-code pairing for remote CLI, mobile and agent clients (no shared passwords; revocable device tokens).
- **URL resolver plugins (M9):** extensible resolvers that extract media from supported sites (yt-dlp backed plus custom resolvers through the plugin system).
- **Tray and launch at startup (M5/M14):** system tray with quick actions and an optional launch-at-startup setting (also offered in the installer).
- **Languages (M14):** English and Simplified Chinese at launch, more through translation files (see 6b).
- **Link handlers and file associations (M14 installer):** `opengrab://` and `magnet:` protocol handlers and `.torrent` (plus `.dlc`, `.crawljob`) file associations, each optional in the installer and in Settings.
- **Search and navigation (M14, basic version in M6):** a command palette (Ctrl+K) and search box that finds features, settings, actions, downloads and plugins by name or keyword, jumps straight to the matching page or setting, and can run actions (e.g. "pause all", "add torrent"). Fuzzy matching, keyboard-first, works offline.

## 7. Licensing
reimplement from public site behaviour; never port JD2 GPL plugin code; no AppWork Utilities; yt-dlp is Unlicense

## 8. Decisions kept
cross-platform Python core, owner token, visible tray/clipboard capture, Docker unverified, local-only by default with HTTPS for remote, port 5678

For M14 (installer): ship a downloadable setup .exe with an installer wizard (Inno Setup or similar). It offers the install location (default C:\Program Files\OpenGrab), an opt-in download of the small local OCR/captcha model plus a toggle to enable it by default, Start menu/desktop shortcuts, autostart, and link/protocol handling. Installed layout is organized (app\, plugins\, models\), user data lives in %APPDATA%\OpenGrab, and a proper uninstaller is registered.


### 6d. Open-source captcha fallbacks (added Oct 28, 2026)
Optional, free, CPU-only fallbacks inserted in the auto solver chain after ddddocr and before paid solvers/manual:
- Second OCR engine for image/text captchas (Tesseract or a small ONNX captcha model), used when ddddocr fails or has low confidence.
- reCAPTCHA audio-challenge solver (Buster-style): request the audio challenge and transcribe it offline with a small speech model (Vosk or Whisper tiny, CPU). Must fail gracefully when Google refuses audio.
- Both opt-in, models downloaded on demand, no GPU required, must run on 2014-era laptops.
- hCaptcha/Turnstile: no OSS solver; fallback remains the real-browser (Edge) solver + saved clearance cookie, then manual.
Scheduled as M8 sub-tasks.

### 6e. Stealth browser solvers (added Oct 28, 2026)
- Replace Playwright in the Edge browser solver with direct Chrome DevTools Protocol (CDP) control (smaller exe, fewer automation fingerprints). Visible window by default; headless not used for Cloudflare.
- Optional Camoufox (hardened Firefox) as a stealthier browser solver, downloaded on demand (~300 MB, opt-in).
- NopeCHA added as a hosted solver option alongside the paid solvers (needs user key).
- Final auto order: ddddocr -> second OCR / Buster-style audio reCAPTCHA -> Edge (CDP) or Camoufox browser solver -> paid solvers (incl. NopeCHA) by priority -> manual last.
Scheduled as M8 sub-tasks with 6d.
