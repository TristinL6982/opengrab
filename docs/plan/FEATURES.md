# OpenGrab feature plan: JDownloader 2 parity and beyond

Drafted 2026-10-27 by Grok Bot for Tristin Liebenberg. Spec basis for Hermes. Sources: `REVIEW.md` (code review of HEAD `9647cd0`, repros in `repro/`), `JD2_PLUGIN_INVENTORY.md` / `jd2_plugins.csv` (JD2 SVN mirror, at least r53761), the JD2 source tree (`org/jdownloader/extensions`), jdownloader.org/home/features and support.jdownloader.org articles. JD2 items marked *unverified* or *uncertain* were not confirmed in source or docs.

## Executive summary

- OpenGrab today covers about **20-25% of JD2's feature set**; its headline differentiator (captcha and Cloudflare) is effectively **0% functional**.
- 92 features compared: Works 10, Partial 23, Broken 17, Stub 3, Missing 39. 30 new features proposed beyond JD2.
- Critical bugs to fix first: stopping or resuming a multi-chunk download produces a corrupt file marked finished (C1, C2); a crash wipes active items from the queue (C3); any website can run commands on the PC through the local API (C4); one bad setting wipes all settings (C5); "Remove link" deletes the downloaded file (C6); the desktop window can load another app and the port drifts (C7); pause blocks the queue (C8).
- The .exe failures come from the wrong Python in `run_opengrab.bat` and `build.bat` (B18, B19), not the package. "Reconnecting to Studio" is another app on the port, not OpenGrab.
- Where OpenGrab can beat JD2: JD2's own support says its browser solver cannot do Cloudflare Turnstile for free; it needs paid services. A real-browser solver with cookie reuse is the clearest win.

## HARD CONSTRAINT: runs on about 99% of machines

Approved by Tristin on 2026-10-27. Every milestone must respect this; a feature that breaks it is rejected.

- **Baseline machine:** 2014-era laptop: dual-core x64 CPU, 4 GB RAM, no usable GPU, Windows 10 x64. The app must install, launch and download normally on it.
- **Base app is light:** modest idle RAM (target under 150 MB for the app process, excluding WebView2), and the installer and exe contain **no ML dependencies** (no torch, tensorflow, onnxruntime, CUDA or bundled models).
- **Local AI captcha solving is optional and OFF by default.** Enabled only by an explicit user opt-in in settings.
- **Tiny CPU-only models:** ONNX run with `onnxruntime` CPU, ideally under 30 MB of models in total; `onnxruntime` and the models are downloaded only on opt-in, never shipped in the installer.
- **No GPU or CUDA dependency** anywhere in the app. GPU acceleration may never be required.
- **Bounded resources:** local AI peak RAM under about 300 MB; models load lazily on the first captcha and unload when idle; inference runs off the UI thread with a timeout.
- **Weak-hardware fallback:** the app detects weak hardware (low RAM, few cores, slow test inference) and skips local AI, falling back to the real-browser solver, then configured solver services, then the manual captcha inbox.
- **Real-browser solver uses the system browser:** drive the installed Edge or Chrome through CDP (Edge ships with Windows 10/11) instead of bundling Chromium where possible; bundled or downloaded Chromium is an optional last resort.
- **Acceptance:** each milestone that touches captcha, browser or packaging must state its RAM, disk and dependency impact, and the default install must pass `--smoke-test` without any ML package present.

Status legend: **Works** (tested or verified), **Partial** (present but incomplete or untested), **Broken** (present, verified wrong), **Stub** (code exists, not wired up), **Missing**. Priority P0 (blocker) to P3 (nice to have).

## Roadmap

Milestone numbering follows the orchestration plan; REVIEW.md labels the first four M1-M4. These replace the older M2-M8 labels in PROGRESS.md.

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

Each milestone is accepted only after the orchestrator re-runs the acceptance commands itself (see REVIEW.md §5).

## Feature comparison with JDownloader 2

### Link grabbing

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Clipboard monitor | Watches the clipboard and sends recognised links to the LinkGrabber; toolbar toggle. | Partial | `app.py:71-96` Win32 ctypes poll every 2 s, GUI mode only, never run on Windows; dedupe only for clipboard. | Event-driven listener (AddClipboardFormatListener), per-host allow rules, visible on/off state, auto-mute while a fullscreen game runs. | P1 | M8 |
| LinkGrabber review | Links grouped into packages with online check, size, names and variants; edit before confirming; optional auto-confirm. | Partial | REVIEW §3: flat candidate list only, no packages, availability check or bulk edit. | Package view with online status, size, ETA and duplicates flagged before adding; inline rename, folder, priority, bulk actions, auto-confirm rules. | P0 | M8 |
| Deep decrypt / page crawl | LinkCrawler with deep inspection (`LinkCrawlerDeepInspector`) and a generic HTTP directory-index crawler. | Broken | B1: any page with links ends in ERROR (`generic.py:122-131`); B2: plain `<a href=x.zip>` links not detected (`generic.py:48-55`). | Depth-limited crawl with file-type filters and a preview of what will be added; directory-index crawler. | P0 | M8 |
| Link filters | LinkGrabber filter rules (host, name, size, type, online status) plus filter views. | Works | REVIEW §3: include/exclude regex/glob works (`filters.py`). | Add size, type, host and online-status conditions, live match counts and a rule test pane. | P2 | M8 |
| Packagizer rules | Condition-based rules that set folder, name, priority, extraction and more. | Works | `test_packagizer.py` 4/4; `{name}` `{stem}` `{ext}` `{host}` templates, preview API. | More conditions (size, source page, package) and actions (priority, password, chunks); live preview on real queue items. | P2 | M8 |
| Click'n'Load v1/v2 | Local listener on 127.0.0.1:9666 (`/flash/add`, `/flash/addcrypted2` AES, `/jdcheck.js`); JD2 has `CNL` and `DummyCNL` crawler classes. | Missing | Nothing listens on 9666; the PROGRESS.md "Click'n'Load ✓" claim is false (REVIEW §4). | CNL2 listener with an origin allowlist and a confirm prompt; payloads land in the LinkGrabber, not straight in the queue. | P1 | M8 |
| Containers DLC / CCF / RSDF | Imports DLC, CCF and RSDF container files (DLC decryption relies on JD's own key service; detail unverified). | Missing | No container code (REVIEW §3). | Import RSDF and CCF locally; DLC only if a public key service is usable (uncertain). Export as plain text/JSON lists. | P2 | M8 |
| NZB / Usenet | `GenericNZBDecrypter` plus 27 Usenet classes with NNTP server accounts. | Missing | No NNTP code. | Optional NNTP engine with yEnc and par2 repair, later. | P3 | M14 |
| Browser extension | MyJDownloader browser extension (Chrome/Firefox) sends links and is required for JD2's browser captcha solver. | Missing | REVIEW §3. | MV3 extension paired by token: send page/link/selection, media sniffing, cookie handoff, captcha handoff. | P1 | M12 |
| Drag-and-drop and paste add | Drag text, links or containers into the LinkGrabber; Add Links dialog. | Works | Code-read: `app.js:576-610` URL box and window drop zone (not exercised in the native window). | Smart paste: dedupe, offline check, size and ETA before adding (see new features). | P1 | M8 |
| Folder Watch | `folderwatchV2` extension watches folders for `.crawljob` files. | Missing | No watcher. | Watch folders for `.txt`, `.crawljob`-style JSON, containers and `.torrent`; documented job format. | P3 | M13 |
| Duplicate detection | Warns when a link is already in the list. | Partial | REVIEW §3: clipboard dedupe only. | Dedupe across queue, history and disk (URL, name+size, hash) with a clear choice. | P1 | M8 |

### Download engine

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Parallel downloads | Max simultaneous downloads plus per-host limits. | Partial | Works at start; runtime change ignored (B12, `manager.py:88`); no per-host limits. | Live-applied limits; per-host and per-account limits. | P1 | M3 |
| Multi-connection chunks | Several connections per file, capped per host by plugins. | Broken | C1 stop marks a corrupt file FINISHED; C2 resume after exit finishes instantly corrupt; B9 failing chunk does not cancel siblings (`downloader.py:416-448`). | Chunk map sidecar fsync'd to disk, never finalise unless every chunk is complete and size matches; split slow chunks dynamically. | P0 | M3 |
| Resume (single stream) | Resumes interrupted downloads. | Works | `e2e_api.py` resume check passes (2.17 MB, single stream). | Keep; also validate ETag/Last-Modified and restart cleanly when the file changed. | P1 | M3 |
| Crash-safe queue | Download list persisted and restored after restart. | Broken | C3: `_save_now()` drops DOWNLOADING links (`manager.py:705-707`); a crash or kill loses them. | Journaled store (SQLite WAL); active items come back as paused with their chunk map. | P0 | M3 |
| Pause and stop | Stop plus a global Pause that drops to a low pause speed (detail unverified). | Broken | C8: pause sleeps inside the worker and blocks the queue (`downloader.py:196-201`); C1: stop corrupts multi-chunk files. | Pause releases the slot and the connection; resume continues from the chunk map. | P0 | M3 |
| Priority and ordering | Priorities, drag reorder, force download. | Partial | Priority dequeue works (`test_queue_controls.py`); move/reorder endpoints claimed but absent (REVIEW §4). | Drag reorder, force start, per-package priority. | P2 | M11 |
| Mirrors / alternative sources | Groups the same file from different hosts and falls back between them (detail unverified). | Missing | No mirror logic. | Auto-group by name+size or hash, fail over automatically, prefer the fastest host. | P2 | M11 |
| Retries and host wait times | Plugin wait times, retry countdowns, skip. | Partial | B8: generic errors retry forever in `_stream()` (`downloader.py:396-404`), `retry_max` ignored. | Bounded retries, per-error policy, visible countdowns for host waits. | P1 | M3 |
| Server filename and sanitising | Uses Content-Disposition and plugin names. | Broken | B11: CD filename effectively unused, unsanitised if used (path traversal), RFC 5987 `filename*` captured raw. | Sanitise every name, honour RFC 5987, show the final name in the LinkGrabber. | P0 | M3 |
| File-exists handling | Ask, skip, overwrite or rename when a file exists. | Partial | Packagizer keeps existing files with numbered names; no user choice. | Per-rule policy with hash compare (skip identical files). | P2 | M11 |
| Hash verification (CRC/MD5/SHA/SFV) | Checks hashes from plugins or SFV after download. | Stub | B14: `verify_hash` setting is never used. | Verify plugin, SFV, `.md5`/`.sha256` and publisher hashes; quarantine mismatches. | P1 | M11 |
| Speed limit | Global and per-download speed limits. | Works | `test_queue_controls.py` shared token bucket; applied only at the next download start. | Live changes, per-download and per-host limits. | P2 | M11 |
| HLS / DASH streams | `GenericM3u8`, YouTube DASH, HDS support. | Partial | Only via yt-dlp, progressive formats only, no ffmpeg merge (B15). | Native HLS/DASH through yt-dlp plus bundled ffmpeg merge. | P1 | M9 |
| Disk and temp handling | Pre-allocation and download folder handling (detail unverified). | Partial | `.part` pre-allocated to full size with zeros (`downloader.py:416-419`), which is the root of C1/C2. | Sparse files plus a chunk map; low-disk guard pauses the queue. | P1 | M3 |

### Extraction

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| ZIP / 7z / TAR | Archive Extractor extension handles common formats including multi-part. | Works | `test_archives.py` 6/6 plus smoke 7z; traversal, symlink and size limits. | Keep safety limits; add multi-volume and nested (deep) extraction. | P2 | M11 |
| RAR (incl. multi-part) | Extracts RAR and multi-part RAR. | Partial | `rarfile` adapter needs external unrar; untested (REVIEW §3). | Bundle a 7-Zip/unrar backend and add a fixture test. | P1 | M11 |
| Archive passwords | Password list plus passwords found with the links. | Works | Opt-in password file tested on encrypted 7z. | Per-package password captured from the source page; remember what worked. | P2 | M11 |
| Post-extract cleanup | Options to delete archives and extract to subfolders. | Partial | Source archive is always preserved; no delete option. | Configurable cleanup (recycle bin, not hard delete). | P3 | M11 |

### Plugins

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Hoster plugins | 1,419 classes covering about 1,700 live hoster sites. | Missing | Only `direct`, `generic`, `ytbridge` exist (`--list-plugins`). | First wave of about 25 hosts (Mega, GDrive, Mediafire, 1fichier, Rapidgator…) plus templates. | P0 | M7 |
| Decrypter / crawler plugins | 858 classes covering 1,165 sites: folders, link protectors, shorteners. | Missing | No crawler plugins. | Folder crawlers for top hosts; Filecrypt and Keeplinks-style protectors; shortener template. | P1 | M7 |
| Template plugins | XFileSharing 315, YetiShare 85, KVS 95, AdLinkFly 21 classes; about 1,550 domains. | Missing | No template base classes. | Declarative YAML templates so one template covers hundreds of hosts (see new features). | P0 | M7 |
| Generic direct HTTP | `DirectHTTP` handles any direct link. | Works | `direct` plugin passes the real-download smoke test. | Keep; add HEAD probing for size and resume support in the LinkGrabber. | P1 | M7 |
| YouTube and media sites | Dedicated plugins: `YoutubeDashV2` with variants, Vimeo, TikTok, Instagram, X, Twitch, SoundCloud and more. | Partial | `ytbridge` uses yt-dlp but progressive formats only, about 360p max on YouTube (B15); dead `_supported_domains()` hack (B21); live sites untested. | yt-dlp with ffmpeg merge, quality presets, subtitles, playlists and channels. | P1 | M9 |
| Dead-host registry | `Offline` plugin marks 2,455 dead domains offline instantly. | Missing | No offline list. | Shipped offline list with instant "host is dead" status. | P2 | M7 |
| Plugin updates | Plugins update separately from the app through the updater. | Missing | No updater. | Signed plugin-pack updates without reinstalling the app. | P1 | M7 |
| User plugin folder | Plugins are built into JD2 (no simple drop-in folder; unverified). | Works | Registry and user plugin dir work (REVIEW §3). | Public SDK with typed API and a test harness (see new features). | P2 | M7 |

### Accounts and debrid

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Account manager | Per-host accounts with validity, traffic left, expiry, enable/disable, cookie and API-key login. | Broken | B6: delete sends `site`, server expects `id`; B7: passwords base64 only; no plugin uses accounts. | DPAPI/keyring storage, account health dashboard with traffic and expiry. | P1 | M4, M10 |
| Premium downloads | Premium mode in plugins: no waits or captchas, more chunks. | Missing | No plugin supports premium. | Premium paths in first-wave plugins. | P1 | M10 |
| Multihost / debrid | 69 multihost plugins incl. Real-Debrid, AllDebrid, Premiumize, TorBox, LinkSnappy. | Missing | No debrid code. | Real-Debrid, AllDebrid, TorBox, Premiumize with automatic routing. | P1 | M10 |
| Account usage rules | Lets you order which account or multihost is used per host (unverified detail). | Missing | No routing rules. | Per-host account order with cost and traffic awareness. | P2 | M10 |
| Cookie login | Cookie login panels for sites that block password login. | Missing | No cookie import. | Import cookies through the paired browser extension. | P2 | M10 |

### Captcha

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Image / text OCR | Own OCR module (JAC) plus a dialog. | Stub | ddddocr solver exists but no plugin feeds it images. | Local OCR with confidence threshold and automatic fallback to manual or service. | P1 | M6 |
| Manual captcha dialog | Dialog for image and click captchas. | Broken | B5: code accepted but never consumed (`manager.py:441-470`); cannot show widgets. | In-app captcha inbox for every type with countdown, skip and solve-all. | P0 | M6 |
| reCAPTCHA v2 | Browser solver (needs the MyJD browser addon) or paid services; 607 plugins incl. inherited. | Missing | No token injection or form submit (REVIEW §3). | Embedded WebView2 widget, token handed back to the plugin; no external browser addon needed. | P0 | M6 |
| reCAPTCHA v3 / Enterprise | Handled per plugin, mostly through solver services (uncertain). | Missing | No support. | Real-browser session scoring plus service fallback. | P2 | M6 |
| hCaptcha | Browser solver or services; 461 plugins incl. inherited. | Missing | B4: wrong 2captcha params (`base.py:192-195`); no widget flow. | Embedded widget plus fixed service params. | P0 | M6 |
| Cloudflare Turnstile | Built-in browser solver cannot solve it; JD2 support says paid services only. 453 plugins incl. inherited. | Missing | No Turnstile flow. | Free solve in an embedded real browser (auto or one click); service fallback. | P0 | M6 |
| Cloudflare challenge (IUAM / managed) | `antiDDoSForHost/Decrypt` handle the old JS challenge; no headless-browser solve for the modern managed challenge. | Missing | Only curl_cffi impersonation in `http_client.py`; downloader ignores it (B10). | Detect challenge, solve in a real browser, reuse `cf_clearance` with the exact UA and TLS fingerprint. | P0 | M6 |
| Other types | Click captcha (10), CutCaptcha (3), Altcha (4), SolveMedia (3), PowCaptcha (1). | Missing | None. | Click and Altcha/Pow first (solvable locally); CutCaptcha via browser. | P2 | M6 |
| Solver services | 10 solver plugins: 2captcha, AntiCaptcha, CapMonster, DeathByCaptcha, 9kw, ImageTyperz and more. | Stub | B3: never reached in auto mode; B4: wrong params; untested. | Working 2captcha, CapMonster, CapSolver with per-type routing and a spend cap. | P1 | M6 |
| Solver order and timeouts | Per-solver wait map, blacklist/whitelist per host. | Broken | B3: `ManualSolver` always returns ok, so paid solvers never run (`base.py:91-95`). | Explicit chain: local → browser → service → manual, with per-host overrides. | P1 | M6 |
| Remote solving | Solve on the MyJD app or browser extension from another device. | Missing | None. | Push captchas to the paired phone PWA. | P2 | M12 |
| 9kw captcha exchange | Supports 9kw community exchange. | Missing | None. | Optional; low value. | P3 | M6 |

### Reconnect

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Router reconnect | HTTP LiveHeader scripts, router database, UPnP, reconnect recorder, test button. | Missing | Acknowledged in README. | UPnP/TR-064 plus script support; lower priority now that most limits are per account. | P3 | M11 |
| External / batch reconnect | Runs an external command or batch script (e.g. `mullvad reconnect`). | Missing | None. | Reconnect hook that runs a VPN CLI and verifies the new IP. | P2 | M11 |
| Auto reconnect on host wait | Reconnects when a host imposes a wait. | Missing | None. | Optional, only with a working reconnect method. | P3 | M11 |

### Scheduling and network

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Scheduler | `schedulerV2`: timed start/stop/pause, speed and max-download changes. | Missing | REVIEW §3. | Time windows plus conditions (metered network, on battery, game running). | P2 | M11 |
| Per-host limits | Per-host max downloads; per-download speed limit. | Missing | REVIEW §3: no per-host limits. | Per-host connections, speed and request rate. | P1 | M11 |
| Proxy manager | Proxy list with HTTP/SOCKS4/SOCKS5, per-host rules, system proxy. | Partial | Single global proxy setting only. | Proxy list, rotation, health check, per-host and per-download binding. | P2 | M11 |

### Automation and extensions

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Event Scripter | `eventscripter`: JavaScript run on download and app events. | Missing | Internal `on_change` events only. | Hooks: sandboxed scripts, webhooks, shell commands (opt-in). | P2 | M13 |
| Shutdown when done | `shutdown` extension: shutdown, standby, hibernate or close after downloads. | Missing | None. | Same, with a cancellable countdown. | P2 | M13 |
| Anti-Standby | `antistandby` stops the PC sleeping while downloading. | Missing | None. | `SetThreadExecutionState` while active. | P1 | M13 |
| Infobar | `infobar` floating speed bar. | Missing | None. | Mini-mode window. | P3 | M14 |
| OS notifications | Bubble notifications; `growl` extension on macOS. | Partial | In-app toasts and list only (`app.js:107-108`), no OS toast. | Native Windows toasts with actions (open folder, solve captcha). | P1 | M11 |
| Chat | `chat` extension (legacy support chat). | Missing | None. | Not planned; link to GitHub Discussions. | P3 | Not planned |

### Remote control

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| MyJDownloader remote | my.jdownloader.org web app and relay for remote control. | Broken | C4: token API is CSRF-exposed to any website. | Secure pairing, per-device keys, revoke (see new features). | P1 | M12 |
| Local API | Local API exists (deprecated JSON API; detail unverified). | Broken | C4: loopback trusted with no token, `ACAO: *`, token leaked by `GET /api/settings`, settings writable, terminal RCE reproduced. | Token on every request, Host and Origin checks, no CORS, whitelisted settings. | P0 | M4 |
| Web UI | Remote web UI only through MyJD. | Partial | Local web UI exists but is unsafe (C4) and cached stale by the service worker (B17). | Secondary UI behind pairing; versioned cache. | P1 | M12 |
| Mobile apps | MyJD Android and iOS apps. | Partial | PWA manifest exists; remote access rejected (B16); stale cache (B17). | Installable PWA over secure pairing. | P2 | M12 |
| Headless / Docker | Headless mode; Docker images are community-made (e.g. jlesage). | Broken | `--headless` works, but the Docker image is unreachable through its port (B16). | Official image with pairing auth. | P2 | M12 |

### UI and UX

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Desktop window | Java Swing desktop GUI. | Broken | C7: window can show another app on 5678/5679, port drifts, `console=True`, no icon. | Native window, no console, nonce-checked server, clear error if the port is taken. | P0 | M5 |
| Tray icon | Tray with minimise-to-tray and quick actions. | Partial | `app.py:128-149`, mock-tested only. | Tray with speed, pause all, clipboard toggle, recent finished. | P1 | M5 |
| Single instance | Second launch hands links to the running instance (unverified). | Missing | C7: no lock; two engines can share `links.json`. | Named mutex; second launch focuses the window or forwards `--grab` URLs. | P0 | M5 |
| Themes | Multiple look-and-feel themes. | Partial | Dark only (`settings.theme`). | Light, dark, follow-system, accent colour. | P2 | M14 |
| Speed graph and stats | Speed meter graph in the status bar. | Partial | `app.js:142` speed-graph canvas, untested. | Per-download and global graphs, history stats. | P3 | M14 |
| Context menus and bulk edit | Extensive context menus; menu and toolbar editor. | Partial | Basic per-row controls only. | Multi-select, bulk edit, right-click menus. | P2 | M14 |
| Keyboard shortcuts | Some shortcuts (extent unverified). | Missing | Only Enter in the URL box (`app.js:576`). | Command palette and full keyboard control (see new features). | P2 | M14 |
| Extra panels: files, terminal, audio, diagnostics | No equivalent in JD2 core. | Partial | Exist, but the terminal is part of the C4 RCE chain. | Keep file browser and diagnostics; terminal desktop-only or removed. | P0 | M4 |

### Settings

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Settings UI | Basic settings panes. | Broken | C5: one invalid value wipes all settings on next start (`settings.py:115-119`). | Validated settings, backup instead of overwrite. | P0 | M4 |
| Advanced settings | Advanced Settings editor exposes every config key. | Missing | JSON file only. | Searchable advanced view with descriptions and reset to default. | P2 | M14 |
| Backup and restore | Settings backup and restore (unverified). | Missing | None. | One-file backup of settings, accounts (encrypted) and queue. | P2 | M14 |

### Updates

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Auto update | Self-updater for app and plugins with restart. | Missing | REVIEW §3. | Signed updates from GitHub Releases, channels, rollback. | P1 | M14 |

### Platforms and packaging

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Windows build / installer | Windows installer with bundled Java. | Partial | Onedir build passes on Linux; Windows unverified; `build.bat` uses `py -3.12` (B19); 447 MB due to ddddocr (B22). | Reproducible build from `.venv`, installer plus portable zip, under 150 MB. | P0 | M2, M5 |
| Run script | Desktop shortcut via installer. | Broken | B18: `run_opengrab.bat` hard-codes the path and uses system Python 3.14 with no deps. | Delete or point at `.venv` / the built exe. | P0 | M2 |
| Tests and CI | Not applicable (internal quality). | Broken | B20: pytest collects 0 tests; parallel resume never tested. | Pytest suite with regression tests and Windows CI building the exe. | P0 | M2 |
| macOS and Linux | Cross-platform Java builds. | Partial | Python core runs on Linux; no packaged builds. | AppImage/Flatpak and a macOS app later. | P3 | M14 |

### Privacy and security

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Credential storage | Accounts stored in local config (encryption detail unverified). | Broken | B7: `"ob:" + base64` (`accounts.py:46-47`). | Windows DPAPI or `keyring`. | P0 | M4 |
| Remove link vs delete file | Separate remove-link and delete-file actions. | Broken | C6: "Remove this link?" deletes the finished file (`manager.py:413-424`). | Remove keeps the file unless "Remove and delete file" is chosen; delete goes to the recycle bin. | P0 | M4 |
| Telemetry | JD2 telemetry and error reporting (unverified). | Works | Code-read: no telemetry or analytics calls found. | Keep zero telemetry as a stated guarantee. | P1 | M14 |

### i18n

| Feature | JD2 | OpenGrab today | Evidence | Target (beats JD2) | Priority | Milestone |
|---|---|---|---|---|---|---|
| Multilingual UI | Multilingual, with a `translator` extension for community translations. | Missing | English-only strings in `app.js`/`index.html`. | String catalogue with community translations. | P2 | M14 |

## New features beyond JD2

| Idea | Rationale | Effort | Priority | Milestone |
|---|---|---|---|---|
| Real-browser Cloudflare solver | JD2 cannot solve Turnstile or managed challenges for free. Solve in the installed system Edge/Chrome via CDP (no bundled Chromium where possible; WebView2 for the in-app widget), cache `cf_clearance` per domain with the matching UA and TLS profile. | L | P0 | M6 |
| Captcha pipeline with local AI (optional, lightweight) | Off by default, opt-in only. Tiny CPU-only ONNX models (onnxruntime CPU, under 30 MB total) for image and click captchas, downloaded on opt-in, lazy-loaded, peak RAM under 300 MB, no GPU or CUDA. Confidence thresholds; weak hardware skips it and falls back to the system-browser solver, services, then the manual inbox. | M | P1 | M6 |
| Captcha inbox | One queue of pending captchas with countdowns, solve-all, desktop toast and phone push, instead of popups that block. | M | P1 | M6 |
| yt-dlp + ffmpeg with quality presets | Covers 1,800+ video and social sites in one plugin. Presets like Best 1080p MP4 or MP3 320, subtitles, chapters, SponsorBlock. | M | P1 | M9 |
| gallery-dl integration | Image galleries and social profiles (albums, artists) that yt-dlp does not cover. | M | P2 | M9 |
| Declarative plugin format | YAML/JSON rules for XFileSharing, YetiShare, KVS and AdLinkFly templates: one template plus a domain list covers hundreds of hosts. | L | P0 | M7 |
| Plugin health dashboard | Scheduled canary checks per host, local error rates, auto-flag broken hosts and show the fix status. | M | P1 | M7 |
| Smart link paste | Dedupe, offline check, size and ETA before anything is added; groups by host and package. | M | P1 | M8 |
| Explainable errors | Every failure says why and what to do (needs account, captcha timeout, host dead, disk full) with a one-click fix. | S | P1 | M8 |
| Auto-categorisation and naming rules | Videos, music, archives and software routed to folders with a live preview of the final path and name. | M | P2 | M8 |
| Torrent, magnet and metalink engine | Optional aria2 or libtorrent backend; magnets are already detected (`plugins/base.py:201`) but nothing downloads them. | L | P2 | M11 |
| Debrid integration | Real-Debrid, AllDebrid, TorBox, Premiumize: unlock hundreds of hosts with one account, including cached torrents. | M | P1 | M10 |
| Per-site rate limits | Requests per minute, concurrency and cooldown on 429 per host, so accounts and IPs do not get banned. | S | P1 | M11 |
| Verify against known hashes | Check publisher checksums, GitHub release sums and SFV; flag tampered or corrupt files. | S | P2 | M11 |
| Game mode and metered-network awareness | Auto-throttle or pause while a fullscreen game runs, on metered Wi-Fi or on battery. | S | P2 | M11 |
| Disk-space guard | Check free space before starting and pause the queue before the disk fills. | S | P1 | M3 |
| Journaled store and download history | SQLite WAL queue that survives crashes, plus searchable history of finished downloads. | M | P0 | M3 |
| Per-download proxy/VPN binding | Bind a download to a proxy or network interface with a kill switch; no telemetry, stated as a guarantee. | M | P2 | M11 |
| Safe file handling | Mark-of-the-Web on downloaded files and an optional Defender scan hook after download. | S | P2 | M11 |
| System tray, native notifications, portable mode | Proper desktop behaviour: tray menu, Windows toasts, and a portable build that keeps data next to the exe. | S | P1 | M5 |
| Onboarding wizard | First run: download folder, browser extension, captcha preferences, accounts, clipboard opt-in. | S | P1 | M14 |
| Command palette and keyboard-first UI | Ctrl+K to add links, pause all, open settings or jump to any download. | M | P2 | M14 |
| Secure remote and mobile UI with pairing | QR-code pairing, per-device keys, revoke, HTTPS or a relay/Tailscale; no open API. | L | P1 | M12 |
| Browser cookie and session handoff | The paired extension shares cookies for sites that need a login, scoped per host. | M | P2 | M12 |
| Public plugin API and SDK | Typed Python API, fixture recorder, test harness and docs so the community can add hosts. | M | P2 | M7 |
| CLI | `opengrab add/list/pause/status` with JSON output for scripting. | S | P2 | M13 |
| Webhooks and integrations | Notify Discord, ntfy or Home Assistant on finish or error. | S | P3 | M13 |
| Import from JD2 | Bring over accounts, link lists, settings and packagizer rules from a JD2 install. | M | P2 | M14 |
| Accessibility and themes | Screen-reader labels, focus order, high contrast, light/dark/system. | M | P2 | M14 |
| Signed releases and delta updates | Windows CI builds, code signing, small delta updates, rollback. | M | P1 | M14 |

Effort: S under a week of agent work, M one to three weeks, L more than three weeks (rough estimates).

## Licensing note (GPLv3)

- JD2 plugin files carry GPLv3-or-later headers (1,343 of 1,422 hoster files); copyright JD-Team.
- Porting or translating JD2 plugin code into Python makes those parts derivative works, forcing GPLv3 for OpenGrab.
- Reimplement from public behaviour instead: site HTML and APIs, URL patterns and domain lists are facts. Use JD2 only to learn which hosts and endpoints exist.
- Avoid AppWork Utilities entirely (non-commercial license, not open source).
- yt-dlp is Unlicense, so wrapping it is safe under any license.
