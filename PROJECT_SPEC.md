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

## 7. Licensing
reimplement from public site behaviour; never port JD2 GPL plugin code; no AppWork Utilities; yt-dlp is Unlicense

## 8. Decisions kept
cross-platform Python core, owner token, visible tray/clipboard capture, Docker unverified, local-only by default with HTTPS for remote, port 5678