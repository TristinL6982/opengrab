# PROJECT_STATUS.md — handoff snapshot

> Written at a pause point (2026-10-25, before a model switch).
> `PROJECT_SPEC.md` is the source of truth for requirements. This file is a
> state snapshot, not a spec. Everything below was verified on this machine
> unless explicitly marked otherwise.

## 1. Overall goal

Build **OpenGrab**: a fully-featured internet download manager (JDownloader-2-style
grabber + downloader) in this repo — `C:\Users\trist\opengrab`.
Portable Python 3.12 core, web UI served on localhost, chunked resumable
downloads, plugin system (direct / generic page / yt-dlp bridge), captcha +
login + paywall handling, filters, accounts. Ship as platform builds
(Windows exe + Linux/macOS) and a Docker image. Full requirements:
`PROJECT_SPEC.md`.

## 2. User decisions (recorded 2026-10-25 — answers to the 6 open questions)

1. **Platforms:** Windows, Linux, macOS via the portable Python core with
   platform-appropriate builds. Do NOT rewrite in Java. (If any doc phrase
   "Java-compatible systems" is meant as Java-only environments, ask the user
   to clarify before treating that as supported.)
2. **Authentication:** v1 = single owner account/token (no multi-user).
   Remote access MUST require authentication.
3. **Tray + clipboard:** Yes — tray icon and LinkGrabber/Click'n'Load
   clipboard capture where the OS supports it. Clipboard monitoring must be
   visible and user-controllable.
4. **Docker:** Supported deployment target. Build & verify where Docker is
   available. This machine has NO docker CLI → the image is UNVERIFIED;
   document that, do not claim verification.
5. **HTTPS:** Local-only by default; HTTPS off on localhost. Remote access
   requires HTTPS via a trusted reverse proxy or explicitly configured TLS.
   Never expose an unauthenticated HTTP service.
6. **Default port:** 5678 (JD2 parity), user-configurable.
   (Current code default is 8765 in `settings.py` — change pending, see M2.)

## 3. What works (verified)

- **Engine** (`engine/manager.py`, `engine/downloader.py`): chunked
  parallel downloads with `Range` resume, `.part` files, pause/stop/resume,
  state persistence (`links.json`). Verified end-to-end below.
- **Server** (`server.py`): ThreadingHTTPServer, HTTP/1.1, JSON API
  (`/api/ping|stats|links|urls|filters|accounts|plugins|settings|events|
  clear-finished|links/{id}/*`), SSE event stream, Bearer-token auth
  (local always allowed; non-local requires `Authorization: Bearer <token>`),
  static UI serving with path-traversal guard.
- **UI** (`ui/static/`): index.html + app.js + style.css + manifest + sw.js +
  svg icon; tabs (queue/active/finished, accounts, filters, plugins,
  settings, captcha modal), drag & drop URL add, service-worker registration,
  Bearer-token handling. All JS API calls match server routes (audited).
- **Supporting modules** (written earlier, exercised via e2e): models,
  settings (pydantic, `%APPDATA%/OpenGrab`), anti-bot `http_client`
  (curl_cffi impersonation), filters, accounts, captcha base+chain,
  plugins base/registry + builtin direct/generic/ytbridge.
- **E2E proof — `tests/e2e_api.py`: 16/16 checks PASS** (isolated temp
  APPDATA, real files.pythonhosted.org downloads):
  ping; all 6 UI assets 200; 9,979 B mdurl wheel → finished with exact
  size + sha256 `84008a41e51615a49fc9966191ff91509e3c40b939176e643fd50a5c2196b8f8`;
  2,172,522 B pydantic_core wheel interrupted mid-transfer → status paused,
  `.part` preserved on disk → resume → finished with exact size +
  sha256 `132529c83901437ff642f585216831bf5fd7a91df66829907e155192ead62498`;
  SSE `hello` event delivered; links persist across a full engine restart.

### Bugs fixed this session (server.py)
1. `start()` returned port 0 for ephemeral binds → now records
   `self.httpd.server_address[1]`.
2. `_static()` resolved request paths against CWD instead of the static root
   → 403 on all UI assets → now anchored to `static_dir().resolve()`.

## 4. What is incomplete (NOT done — do not claim otherwise)

- **Entry points:** no `cli.py` / `__main__.py` / `smoke.py` →
  `python -m opengrab --smoke-test` (spec §5) does not exist yet.
  (`tests/e2e_api.py` covers the same ground but is not wired up.)
- **GUI:** no `app.py` — no pywebview window, no tray, no clipboard
  Click'n'Load watcher. (Deps ARE installed and importable — see §7.)
- **API gaps vs spec §2:** `/api/health` (alias of `/api/ping`),
  `/api/captcha-test`, `/api/notifications`, `/api/files` (file manager
  list/mkdir/delete/rename/download/upload), `/api/terminal`,
  `/api/clipboard`, `/api/audio` (Range streaming), base_path +
  reverse-proxy header support. Multi-user auth (`/api/auth/users`)
  intentionally OUT for v1 per decision #2.
- **UI gaps:** no notifications tab, no audio player, no PWA PNG icons
  (svg only).
- **Packaging:** no PyInstaller spec/build script → no `OpenGrab.exe`;
  no `Dockerfile`/`docker-compose.yml`/`.env.example` (and unbuildable here —
  no docker CLI); no `README.md`.
- **Unit tests:** none (only e2e + `tests/probe_curl.py` probe).
- **Default port** is 8765 in code; decision #6 wants 5678.
- `plugins/builtin/ytbridge.py` has a known dead loop in
  `_supported_domains()` (`ie._match_valid_url.__code__ and []` hack) —
  clean up when touching that file.

## 5. Key design decisions & constraints (standing)

- `PROJECT_SPEC.md` is authoritative; preserve working code; verify each
  milestone before moving on; update PROGRESS.md with evidence; commit after
  each verified milestone (AGENTS.md).
- Auth model (matches decision #2 today): local requests (127.0.0.1/::1)
  always allowed; anything else needs the Bearer token. Token auto-generated
  on first run and persisted in settings.
- State: `%APPDATA%/OpenGrab/` (`settings.json`, `links.json`, `plugins/`,
  `downloads` dir configurable). Resume = `.part` file + `Range: bytes=N-`.
- Server: HTTP/1.1 keep-alive; SSE at `/api/events` (client must read
  line-oriented; `read(n)` on a keep-alive stream with no Content-Length
  blocks — that was a test bug, not a server bug).
- Environment (verified): Windows 11, git-bash terminal (POSIX syntax;
  native tools need `C:/...` paths). Use `.venv` (Python 3.12) — do NOT use
  the system `python` (3.14). DNS is filtered: hetzner domains do NOT
  resolve; pypi.org / files.pythonhosted.org DO.

## 6. Files changed at this pause point (git state)

Uncommitted (all part of the verified milestone, safe to commit):
- `M  PROGRESS.md` — refreshed build-state doc (user-updated)
- `M  src/opengrab/settings.py` — added `api_host="127.0.0.1"`,
  `api_port=8765` (→5678 pending), `api_token=""`
- `?? src/opengrab/server.py` — new ApiServer (incl. the two bugfixes above)
- `?? src/opengrab/ui/` — index.html, app.js, style.css, manifest.webmanifest,
  sw.js, opengrab.svg
- `?? tests/e2e_api.py` — 16-check e2e suite
- `?? AGENTS.md`, `?? PROJECT_SPEC.md` — user-provided docs (untracked)

## 7. Commands / checks already run + results

| Check | Result |
|---|---|
| `PYTHONPATH=src .venv/Scripts/python.exe tests/e2e_api.py` | **16/16 PASS, exit 0** |
| `sha256sum` of mdurl wheel (9,979 B) | matches test constant (above) |
| `pip list` (.venv) | curl_cffi 0.16.3, httpx 0.28.1, pydantic 2.14.1, pyinstaller 6.22.3, pystray 0.19.5, pywebview 6.2.1, pythonnet 3.2.1, bottle 0.13.4, yt-dlp 2026.10.21, ddddocr 1.6.1, opencv-python 5.0.0.93 |
| `import webview` (pywebview 6.x import name) | **ok** (note: `import pywebview` FAILS — wrong module name; use `webview`) |
| `import pystray` | ok (no `__version__` attr; that's normal) |
| `docker --version` | **not found** — Docker unavailable on this machine |
| UI JS ↔ server API endpoint audit (grep) | all calls match |
| Direct engine probe (mdurl URL → finished in ~3 s) | ok (earlier this session) |

## 8. Next concrete steps (resume order)

0. **Commit the verified milestone** (§6 files) with a clear message — this
   pause point. (Do this first so the switch loses nothing.)
1. **M2 — entry points:** `cli.py` + `__main__.py` with `--smoke-test`
   (wrap `tests/e2e_api.py`), `--version`, `--list-plugins`, `--headless`,
   `--port/--bind`. Change default `api_port` 8765 → **5678** (decision #6).
   Verify `python -m opengrab --smoke-test` exits 0.
2. **M3 — API completeness:** `/api/health` alias, `/api/notifications`,
   `/api/captcha-test`, `/api/files`, `/api/clipboard`, `/api/audio`,
   base_path + X-Forwarded-* handling. Keep token enforcement for all
   non-local callers (decisions #2/#5).
3. **M4 — GUI:** `app.py` with pywebview window (`import webview`), pystray
   tray, Click'n'Load clipboard watcher — watcher must be visible in UI and
   user-toggleable (decision #3).
4. **M5 — UI completion:** notifications tab, audio player, PWA PNG icons
   (generate with Pillow).
5. **M6 — Windows packaging:** `build/OpenGrab.spec` + `build/build.bat`
   (PyInstaller onedir) → verify `OpenGrab.exe` boots and serves UI here.
6. **M7 — Docker:** `Dockerfile`, `docker-compose.yml`, `.env.example` per
   spec §4. This machine cannot build/verify (no docker CLI) → mark
   UNVERIFIED in docs (decision #4).
7. **M8 — Docs & hardening:** `README.md` (JD2-docs section parity), unit
   tests (downloader resume math), fix ytbridge `_supported_domains()` dead
   loop, final acceptance pass against spec §5 checklist.
