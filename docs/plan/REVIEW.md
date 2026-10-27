# OpenGrab — independent code review

Reviewed: `git archive` HEAD `9647cd0` (13 commits, latest "feat: enforce queue priority and global bandwidth cap") plus working-tree docs.
Reviewer environment: Linux box, fresh venvs (Python 3.13.5 system and Python 3.12.15 via uv). Original sources untouched; runs were done in copies (`run/`, `run312/`).
Repro scripts: `/workspace/opengrab/repro/` (`resume_parallel.py`, `restart.py`, `misc.py`).
Date: 2026-10-27.

> Note: HEAD **does** contain `__main__.py`, `cli.py`, `app.py`, `smoke.py`, `build/OpenGrab.spec`, `build/build.bat`. Whatever the uncommitted working-tree changes on the PC are, they are not reviewed here.

---

## 0. What actually runs (raw results)

| Command (Linux, fresh venv) | Result |
|---|---|
| `pip install -e .` (py3.13 and py3.12) | OK |
| `python -m opengrab --version` | `OpenGrab 0.1.0`, exit 0 |
| `opengrab --version` (console script) | OK |
| `python -m opengrab --list-plugins` | `direct`, `generic`, `ytbridge` (3 plugins total) |
| `python -m opengrab --smoke-test` | `OpenGrab smoke test passed (plugins, API, UI, download, SHA-256, 7z).` exit 0 |
| `python -m opengrab --headless` | serves on 127.0.0.1:5678; 2nd instance → `OpenGrab failed: [Errno 98] Address already in use` |
| import every module (25) | all OK |
| `pytest -q tests` | **`no tests ran` — 0 collected.** All `tests/test_*.py` are scripts with `main()`, not pytest tests |
| each test script run directly | all pass: archives 6/6, ranges 2/2, gui_lifecycle (mock) PASS, packagizer 4/4, queue_controls 3/3, ytbridge 3/3, `e2e_api.py` 16/16, `e2e_api_m3.py` 32/32 |
| PyInstaller with `build/OpenGrab.spec` (Linux, py3.12) | Build complete; 447 MB onedir; frozen `--version`, `--list-plugins`, `--smoke-test` all pass; `ui/static` bundled correctly |
| PyInstaller with system py3.13 | fails: `Python shared library ('libpython3.13.so.1.0') was not found` (box environment, not the project) |

**Not testable here (Windows-only / GUI):** pywebview + WebView2 window, pystray tray, Win32 clipboard ctypes path, `build.bat` (`py -3.12` launcher), the real `OpenGrab.exe` GUI launch. The GUI test (`test_gui_lifecycle.py`) only mocks `webview`/`pystray`, so it tells you nothing about the real window.

**Takeaway:** the core package, CLI and spec are mechanically sound. The Windows failures you saw (`No module named opengrab`, an .exe build failing with "no pip") are explained by the **interpreter used**, not by the package: `run_opengrab.bat` calls bare `python` (= the 3.14 interpreter with no deps, per PROJECT_STATUS §5), and `build.bat` calls `py -3.12`, which is not the project `.venv` that has PyInstaller. The tests passing does **not** mean the product works. They miss the serious bugs below.

---

## 1. Critical blockers (verified by running code)

### C1. Stopping a multi-chunk download marks it FINISHED with a corrupt file
Files ≥ 4 MiB use parallel chunks by default (`per_link_chunks=2`). When you press Stop, the chunk threads raise `DownloadError("cancelled")`. The `_parallel()` worker swallows it (`engine/downloader.py:434-437`: the error is only recorded `if not e.cancelled and not self.stop_event.is_set()`), so `errors` is empty and `_parallel` returns normally. `run()` then calls `_finalize()` and renames the `.part` file to the final name. That `.part` was pre-allocated to full size with zeros (`downloader.py:416-419`).
```
progress before stop: 0.38
status 3s after stop: finished | path: .../dl/blob.bin | progress: 1.0
'finished' file size: 8388608 sha256 match: False | zero 64KiB blocks: 76 of 128
```
(`repro/resume_parallel.py`, 8 MiB local range server, `per_link_chunks=4`)

### C2. Resuming a multi-chunk download after app exit "finishes" instantly with a corrupt file
On resume, `run()` treats `part_size` as bytes done (`downloader.py:276-287`). Because the part file was truncated to full size, `_done >= _total` and it finalizes immediately. No per-chunk offsets are persisted. The docstring claim "each chunk re-issues Range from the last byte it wrote" (`downloader.py:10-11`) is false across restarts.
```
[graceful] links.json entries after exit at ~35%: ['paused']
[resume] status: finished after 0.05s | sha256 match: False | zero 64KiB blocks: 76
```
(`repro/restart.py graceful`)

### C3. A crash or kill during a download deletes the link from the queue
`_save_now()` drops every link whose status is DOWNLOADING (`engine/manager.py:705-707`). Only a graceful `shutdown()` flips them to PAUSED first. A crash, Task Manager kill, power loss or BSOD therefore loses every active item. The recovery branch in `_load_links` (`manager.py:725-727`) is dead code.
```
[crash] links.json entries after exit at ~35%: []
[resume] links loaded: []
```

### C4. Any website the user visits can get remote code execution through the local API
- `authorize()` trusts every loopback request with no token (`server.py:227-234`).
- Every response sends `Access-Control-Allow-Origin: *` (`server.py:276-281`), and OPTIONS returns 204 for any origin (`server.py:430-433`).
- `GET /api/settings` returns the `api_token` (`server.py:840-844`).
- `POST /api/settings` sets **any** attribute with no validation, including `terminal_enabled` and `download_dir` (`server.py:1023-1034`).
- There is no Host-header check, so DNS rebinding also works (`Host: attacker.example` → 200).

Chain reproduced with `Origin: https://evil.example`:
```
1. preflight → 204, Access-Control-Allow-Origin: *, Allow-Headers: Content-Type, Authorization
2. GET /api/settings → 200, ACAO *, token leaked: 5a6ded4e…
3. POST /api/settings {"terminal_enabled":true} (no token) → {"ok": true}
4. POST /api/terminal (Bearer leaked token) {"command":"id; echo PWNED"} → output "uid=1000(box) ... PWNED"
5. POST /api/settings {"download_dir":"/"} → GET /api/files?path=etc lists /etc
```
Even without the terminal, step 5 plus `PUT /api/files/upload` lets an attacker write anywhere, for example into the Windows Startup folder. Browser Private-Network-Access protections are not reliable mitigation (Firefox does not enforce them, and Chrome's rollout is partial).

### C5. One invalid setting silently wipes all settings on next start
`POST /api/settings {"max_parallel":"lots"}` is persisted as-is because pydantic does not validate assignment. On the next launch, `load_settings()` fails validation, swallows the error (`settings.py:115`), and overwrites the file with defaults (`settings.py:119`). Observed: token reset to `''`, `download_dir` reset, `max_parallel` reset to 3.

### C6. "Remove this link?" deletes the downloaded file from disk
`remove_link()` adds `link.path`, the finished file, to its delete list (`manager.py:413-424`). The UI prompt only says "Remove this link?" (`app.js:449`).
```
A. finished: finished | file exists: True
A. after 'Remove this link?': file still exists: False
```

### C7. Desktop launch can show another app, and the port silently drifts
- `start_server()` saves whatever port was used into `settings.json` (`app.py:42-43`). A one-off `--port 5679` becomes the permanent default. Verified: `"api_port": 5679` after one run. **This is the 5679 mechanism.**
- `ThreadingHTTPServer` sets `SO_REUSEADDR` (`HTTPServer.allow_reuse_address = 1`). On **Windows** that lets OpenGrab bind a port another process already listens on, so connections to `127.0.0.1:<port>` can reach the other app. The window then loads `http://127.0.0.1:{bound_port}/` (`app.py:101-104`) without checking that it is talking to OpenGrab.
- The text "Reconnecting to Studio…" does **not** exist anywhere in OpenGrab. Its only reconnect text is `reconnecting…` (`app.js:89`). The window was rendering a different local web app. Most likely cause: another program on 5678/5679 plus Windows port sharing. *(Hypothesis; check on the PC with `netstat -ano | findstr :5678` and `:5679`, then `Get-Process -Id <PID>`.)*
- There is no single-instance lock, so a second launch on Windows can co-bind and run two engines against the same `links.json`.
- `OpenGrab.spec:31` uses `console=True`, so a console window opens with the app and closing it kills the app. There is no `.ico` either. This is not a "proper application".

### C8. Pausing a download blocks the queue
Pause just sleeps inside the chunk loop (`downloader.py:196-201`). The worker thread stays occupied and the HTTP connection stays open. With `max_parallel=1`:
```
C. paused slow link: paused
C. new link 6s after pausing another (max_parallel=1): waiting
```
With the default of 3, three paused items freeze the whole queue.

---

## 2. Bugs (code-read and/or verified)

| # | Location | Bug |
|---|---|---|
| B1 | `plugins/builtin/generic.py:122-131`, `manager.py:212,319` | Any web page with download links ends in **ERROR** ("page has child links; pick one"), because `generic.resolve()` raises before `_resolve()`'s `elif link.children` branch. Verified: `status: error … children: 2`. |
| B2 | `generic.py:48-55` | Plain `<a href="x.zip">Name</a>` links are not detected, because the extension is tested against `href + " " + text`. Verified: 0 children for a page with two .zip links. |
| B3 | `captcha/base.py:91,94-95` vs `:174,:284` | `ManualSolver` (priority 50) always returns `ok=True "__MANUAL__"`. In `auto` mode, paid solvers (priority 100) are **never reached**. Verified: order `['manual','2captcha']`, result `__MANUAL__` with a 2captcha key set. |
| B4 | `captcha/base.py:192-195` | 2captcha hCaptcha/Turnstile params use `hcaptchakey` / `tsitekey`; the 2captcha in.php API expects `sitekey`. Untested against the real service. |
| B5 | `manager.py:441-470` + `generic.py` | A manual captcha code is accepted but no plugin ever uses `ctx.manual_code` or submits it. The captcha flow is a dead end. |
| B6 | `app.js:341,506` vs `server.py:1055-1057`, `accounts.py:68` | Account delete sends `site`, but the server deletes by `id`. **Accounts can never be removed from the UI.** |
| B7 | `accounts.py:46-47` | Passwords are "encrypted" with base64 (`"ob:" + b64`). Use Windows DPAPI or `keyring`. |
| B8 | `downloader.py:396-404` | Generic exceptions in `_stream()` retry **forever** (`while True`, backoff capped at 30 s). `retry_max` is ignored at this level. |
| B9 | `downloader.py:427-448` | One failing chunk does not cancel its siblings; they keep downloading until done. |
| B10 | `downloader.py:36,109,342` | The downloader hard-codes `impersonate="chrome"` and `timeout=60`, ignoring `settings.impersonate`, `user_agent`, `ua_rotation` and `timeout_s`. The grab and the download can therefore have different fingerprints, which breaks Cloudflare-cleared sessions. |
| B11 | `downloader.py:268-274` | The Content-Disposition filename is effectively never used (the manager always sets `link.filename` first, `manager.py:556-581`). If it were used, `self.part` is built from the **unsanitized** name (path traversal). The RFC 5987 `filename*=UTF-8''…` form is captured raw. |
| B12 | `manager.py:88` | The worker count is fixed at start, so changing `max_parallel` in Settings has no effect until restart. |
| B13 | `manager.py` (link mutation) | `Link` objects are mutated from worker, grab and tick threads without `_lock`. `_persist`'s `_persist_scheduled` flag is racy. Low impact today, but it is a data race. |
| B14 | `settings.py:verify_hash` | The setting exists but is never used. No CRC/MD5/SFV checking. |
| B15 | `ytbridge.py:149-165` | Only progressive formats are supported; separate video+audio formats fail on purpose (no ffmpeg merge). On YouTube that means about 360p maximum. |
| B16 | `server.py:248-261`, `Dockerfile` | Any non-loopback client is rejected even with the token. Verified via 172.30.0.2: `{"error":"remote access requires HTTPS"} [403]`, also with `X-Forwarded-Proto: https`. **The Docker image is unreachable through its published port.** |
| B17 | `ui/static/sw.js` | Cache-first service worker with a fixed cache name `opengrab-ui-v4`. UI updates are not picked up unless someone bumps the name. |
| B18 | `run_opengrab.bat` | Hard-coded `C:\Users\trist\opengrab`, uses system `python` (3.14, no deps), calls `pause`. It should be deleted or rewritten to use `.venv`. |
| B19 | `build/build.bat` | Uses `py -3.12`, not `.venv\Scripts\python.exe`, which explains "no pip / no PyInstaller". |
| B20 | `tests/*` | The tests are not pytest-collectable (`pytest` → 0 tests). `e2e_api.py` only isolates through `APPDATA`, so on Linux/macOS it writes into the real user data directory. Its resume test uses a 2.17 MB file, below the 4 MiB parallel threshold, so **parallel resume was never tested**. |
| B21 | `ytbridge.py:44` | Known dead `_supported_domains()` hack, still present. |
| B22 | spec | 447 MB onedir because `collect_all("ddddocr")` pulls in onnxruntime and opencv. Lazy-load ddddocr or ship it as an optional component. |

---

## 3. JD2 parity table

Legend: ✅ Present+works (tested) · ⚠️ Present but broken/partial/untested · 🟡 Stub · ❌ Missing

| Feature | Status | Evidence |
|---|---|---|
| Native desktop app (.exe, window) | ⚠️ | Spec builds (Linux verified). Windows GUI unverified, `console=True`, port hijack/drift (C7) |
| Tray icon | ⚠️ | `app.py:128-149`, mock-tested only |
| Clipboard monitor → LinkGrabber review | ⚠️ | `app.py:71-96`, `server.py:172-199`. Win32 path untested; GUI mode only |
| LinkGrabber (packages, online check, sizes, rename before add) | ⚠️ | Flat candidate list only. No packages, availability check or bulk edit. Page crawl broken (B1, B2) |
| Click'n'Load v1/v2 (port 9666, `/flash/addcrypted2`, `/jdcheck.js`) | ❌ | Not in code (PROGRESS claims "Click'n'Load ✓") |
| Browser extension | ❌ | — |
| Container import DLC / CCF / RSDF | ❌ | — |
| Multi-connection chunked downloads | ⚠️ broken | C1, C2, B9 |
| Resume (single stream) | ✅ | `e2e_api.py` resume check passes |
| Crash-safe queue persistence | ⚠️ broken | C3 |
| Global speed limit | ✅ | `test_queue_controls.py`; applied only at the next download start |
| Max parallel downloads | ⚠️ | Works at startup; runtime change ignored (B12); no per-host limits |
| Pause / stop / priority | ⚠️ | Priority ✅; pause blocks workers (C8); stop corrupts (C1) |
| Scheduler (time windows, speed profiles) | ❌ | — |
| Reconnect (router / IP change) | ❌ | Acknowledged in README |
| Host/decrypter plugin system | ⚠️ | Registry + user plugin dir work. **Only 3 plugins**, no hoster or decrypter plugins (JD2 has ~1000s) |
| yt-dlp integration | ⚠️ | Progressive formats only, no ffmpeg merge (B15); live sites untested |
| Account manager (premium, traffic, validity) | ⚠️ broken | Add works; delete broken (B6); base64 creds (B7); no plugin uses accounts |
| Captcha: image OCR (ddddocr) | 🟡 | Solver exists; no plugin feeds it images |
| Captcha: manual dialog | ⚠️ broken | Image + text box only; code never consumed (B5); cannot show reCAPTCHA/hCaptcha/Turnstile widgets |
| Captcha: solver services (2captcha, CapMonster, CapSolver) | 🟡 | Never reached in auto (B3); wrong params (B4); never tested |
| reCAPTCHA / hCaptcha token flow | ❌ | No token injection or form submit anywhere |
| **Cloudflare challenge / Turnstile** | ❌ | Only curl_cffi TLS impersonation in `http_client.py`. No challenge detection, browser solve or `cf_clearance` handoff. Downloader ignores impersonation settings (B10) |
| Extraction ZIP / 7z / TAR | ✅ | `test_archives.py` 6/6 + smoke 7z |
| Extraction RAR | ⚠️ | Adapter only; needs external unrar; untested |
| Archive passwords | ✅ | Password-list file (opt-in) tested on 7z |
| Hash verification (CRC / SFV / MD5) | 🟡 | `verify_hash` setting unused |
| Proxies | ⚠️ | Single global proxy; no list, rotation, per-host or SOCKS UI |
| Packagizer (rename / subfolder rules) | ✅ | `test_packagizer.py` 4/4 |
| Link filters | ✅ | Include/exclude regex/glob works |
| Notifications | ⚠️ | In-app list only; no OS toast |
| Settings UI | ⚠️ | Exists; no validation, can wipe config (C5) |
| Remote control (MyJD-like) / Docker | ⚠️ broken | Token API exists but is CSRF-exposed (C4); Docker unreachable (B16) |
| Auto-update | ❌ | — |
| Duplicate detection | ⚠️ | Clipboard dedupe only |

**Score (35 rows):** 6 ✅ works (~17%) · 18 ⚠️ present-but-partial/broken · 3 🟡 stub · 8 ❌ missing.
Counting partial credit, OpenGrab realistically covers **about 20–25% of JD2's feature set**. On the user's headline differentiator, captcha and Cloudflare, it is effectively **0% functional**.

---

## 4. False or misleading claims in the docs

| Doc | Claim | Reality |
|---|---|---|
| PROJECT_STATUS §3 | "chunked parallel downloads with Range resume, .part files, pause/stop/resume … **Verified end-to-end**" | The e2e test only covers single-stream (2.17 MB < 4 MiB threshold). Parallel stop/resume corrupts files (C1, C2) |
| PROJECT_STATUS §3 / §7 | "All JS API calls match server routes (audited)" | Account delete sends `site`, server expects `id` (B6) |
| PROJECT_STATUS §3 / §5 | "Bearer-token auth (local always allowed …)", terminal "requires the bearer token even locally" | True as written, but the token is served cross-origin to any website, so it gives no protection (C4) |
| PROGRESS (parity list) | "Click'n'Load ✓ (tray+clipboard watcher)" | Clipboard polling is not Click'n'Load. Nothing listens on 9666 |
| PROGRESS inventory §4 | "web auth (pbkdf2 hash + token cookie), optional HTTPS (self-signed or user cert)", "move, reorder" endpoints | None of these exist in `server.py` |
| PROGRESS inventory §9 | "console=False for GUI" | `OpenGrab.spec:31` has `console=True` |
| PROGRESS inventory §3 | Manager provides "speed/eta smoothing, state persistence (state.json) … notifications list" | State file is `links.json`, and it drops active downloads (C3) |
| PROGRESS / PROJECT_STATUS | `--smoke-test` described as covering "ranged resume check" (§7 of inventory) | `smoke.py` does no resume test |
| PROJECT_STATUS §4 | "Tray and … clipboard capture remain unimplemented", "no PyInstaller spec", "no README", m3 "19/19" | Stale and self-contradictory: these exist, and m3 has 32 checks |
| Captcha chain docstring (`chain.py:4-7`) | auto = "ddddocr → manual → 2captcha → …", "escalate" | Manual always "succeeds", so there is no escalation (B3) |
| README "Remote access" / Docker section | Remote access works through an HTTPS proxy | A non-loopback proxy hop is rejected (B16) |
| README limitations | "The Windows onedir executable passes `--version`, plugin discovery, and the real-download smoke test" | Plausible (it passes on Linux), but the user observed a broken .exe build and a broken GUI. Unverified on Windows by this review |

---

## 5. Recommended milestone order for Hermes

Each milestone needs **raw command output pasted back**. The orchestrator re-runs the acceptance commands itself before accepting. On Windows, all commands use `.venv\Scripts\python.exe` (never bare `python` or `py`).

### M1 — Build and test hygiene (small, do first)
- Convert `tests/*.py` into pytest tests, keeping the scripts runnable. Add `pyproject.toml` with `[tool.pytest.ini_options] testpaths=["tests"]`.
- Copy `/workspace/opengrab/repro/*.py` into `tests/regression/` as **currently failing** tests (mark them `xfail(strict=True)` until M2 fixes them).
- Rewrite `build/build.bat` to use `..\.venv\Scripts\python.exe -m PyInstaller`. Delete `run_opengrab.bat`, or rewrite it to call `.venv\Scripts\OpenGrab` or the built exe.
- Add a GitHub Actions workflow on `windows-latest` that runs pytest, builds the exe, and runs `OpenGrab.exe --smoke-test`.

**Acceptance:**
```
.venv\Scripts\python.exe -m pytest -q            # >= 30 tests collected, 0 failed (xfails allowed)
build\build.bat && build\release\OpenGrab\OpenGrab.exe --version
build\release\OpenGrab\OpenGrab.exe --smoke-test # exit 0
gh run list -R TristinL6982/opengrab -L 1        # CI green
```

### M2 — Download integrity (C1, C2, C3, C8, B8, B9, B11, B12)
- Persist per-chunk progress in a sidecar (`<file>.part.json` holding chunk ranges and bytes written, fsync'd periodically). On resume, read it and resume each chunk. Never finalize unless every chunk is complete and the size matches.
- When a chunk is cancelled, raise cancel out of `_parallel`, never finalize.
- Persist DOWNLOADING links (as PAUSED on load) and keep the `_load_links` recovery path.
- Pause must release the worker and the connection (stop the stream, keep the sidecar, requeue on resume).
- Bound `_stream` retries by `retry_max`. Cancel sibling chunks on fatal error. Sanitize the Content-Disposition name and honor RFC 5987 `filename*`. Apply `max_parallel` live.

**Acceptance:**
```
.venv\Scripts\python.exe -m pytest -q tests/regression   # resume_parallel, restart(crash+graceful), pause-slot: all PASS, xfail markers removed
.venv\Scripts\python.exe tests\e2e_api.py                 # still 16/16
```
Orchestrator spot check: queue a ≥200 MB file with ≥4 chunks, kill the process in Task Manager at ~40%, relaunch, resume, then compare `certutil -hashfile <file> SHA256` with the source.

### M3 — Local API security + settings safety (C4, C5, C6, B6, B7)
- Remove `Access-Control-Allow-Origin: *`; send no CORS by default.
- Require the token on **every** `/api/*` request, including loopback. Hand the token to the pywebview window through `js_api` or an HttpOnly SameSite=Strict cookie set on first load with a per-launch nonce.
- Validate `Host` against `127.0.0.1` / `localhost` / configured names. Reject requests whose `Origin` is not same-origin.
- Stop returning `api_token` from `GET /api/settings`.
- Turn on `validate_assignment=True` in Settings and whitelist which keys `POST /api/settings` may change. On a bad `settings.json`, back it up and **do not** overwrite it.
- `terminal_enabled` may only be changed from the desktop UI (or remove the terminal).
- Remove-link must not delete files unless the user explicitly chooses "Remove and delete file".
- Fix account delete to use the id. Store credentials with Windows DPAPI or `keyring`.

**Acceptance (each must fail closed):**
```
curl -si -H "Origin: https://evil.example" http://127.0.0.1:5678/api/settings      # 401/403, no ACAO header, no api_token in body
curl -si -X OPTIONS -H "Origin: https://evil.example" http://127.0.0.1:5678/api/settings  # no Access-Control-Allow-Origin
curl -si -H "Host: attacker.example" http://127.0.0.1:5678/api/links               # 403
curl -si -d "{\"terminal_enabled\":true}" -H "Content-Type: application/json" http://127.0.0.1:5678/api/settings  # 401/403
POST /api/settings {"max_parallel":"lots"} with token -> 400; restart -> settings.json unchanged
pytest test: remove link keeps the finished file; account delete removes the account
```

### M4 — Desktop app that launches reliably (C7, B22)
- Single-instance lock: a named mutex on Windows; a second launch focuses the existing window or sends it `--grab` URLs.
- On Windows, bind with `SO_EXCLUSIVEADDRUSE` (override `server_bind`) and do not set SO_REUSEADDR. If 5678 is taken, show a clear dialog, or pick a free port **without persisting it**.
- Never write `api_port` from a CLI `--port` override (remove `settings.save()` at `app.py:42-43`).
- Before `create_window`, GET `/api/ping` and require a per-launch nonce to match. Otherwise abort with an error.
- `console=False` plus an `.ico`; write a log file under `%APPDATA%\OpenGrab\logs`. Detect a missing WebView2 runtime and show a message.
- Lazy or optional ddddocr to shrink the bundle.

**Acceptance (orchestrator runs on the PC and takes a screenshot):**
```
netstat -ano | findstr :5678      # before: identify any foreign listener
build\release\OpenGrab\OpenGrab.exe     # window title "OpenGrab", OpenGrab queue UI visible (screenshot), no console window, tray icon present
start it a 2nd time                    # no second process (tasklist /fi "imagename eq OpenGrab.exe" shows 1)
python -m http.server 5678 (occupy port) then launch -> clear error or alt port; settings.json api_port still 5678
```

### Later milestones (in order)
- **M5 Cloudflare and captcha (headline feature).** Detect CF challenge pages ("Just a moment", `cf-mitigated`, 403/503 with challenge markers). Solve in an embedded pywebview or WebView2 window that the user can interact with (Turnstile and interactive challenges). Harvest `cf_clearance` plus the exact UA, and hand both to curl_cffi with matching impersonation (fix B10). Optional FlareSolverr backend. Add a real reCAPTCHA/hCaptcha/Turnstile widget-harvest dialog. Fix the chain order (B3) and 2captcha params (B4). Make `ctx.manual_code` actually consumed. Acceptance: local fixture pages for each captcha type, plus a live run against a CF-protected test site shown to the orchestrator.
- **M6 LinkGrabber v2.** Fix B1 and B2. Add packages, online/size check, a Click'n'Load HTTP listener on 127.0.0.1:9666 (`/jdcheck.js`, `/flash/add`, `/flash/addcrypted2` with the AES key/jk handling), and DLC/RSDF/CCF import. Acceptance: post a known CNL2 payload with curl and see the links appear.
- **M7 yt-dlp full.** Bundled ffmpeg merge, format picker, live tests on 3 sites.
- **M8 Scheduler, proxies list/rotation, per-host limits, OS notifications, hash/SFV verify, RAR fixture test.**
- **M9 Browser extension** (Chromium MV3) that sends to the local API with a pairing token.
- **M10 Hoster/decrypter plugin pack and account premium support.**
