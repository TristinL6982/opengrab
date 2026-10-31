# OpenGrab: verified state

> Status note, rewritten 2026-10-30 after M14. [project-spec.md](project-spec.md) is the source of
> truth; its section 9 lists open decisions and known gaps. Earlier versions of this
> note are in git history. Each line below says how it was verified.

## How to check it yourself

```
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
cd build && ..\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --distpath release OpenGrab.spec
build\release\OpenGrab\OpenGrab.exe --smoke-test
```

Tests may not be deleted, skipped to hide a failure, or marked xfail. The one
xfail is known bug B1 (a generic page with file links ends in ERROR).
Platform skips on Linux and macOS cover Windows-only features only.

## Latest verification (2026-10-30)

| Check | Result |
|---|---|
| Full test suite, Windows 11 | 929 passed, 1 xfailed (B1) |
| CI on GitHub | Green on 2026-10-30: Windows (tests, exe, smoke test, setup built, silent install, smoke test of the installed copy, uninstall), Ubuntu (under a virtual display) and macOS test suites with Windows-only tests skipped, Docker image healthy and token-protected |
| Onedir exe | 70 MB (unused image codecs, Tk and other platforms' webview backends excluded), no ML or libtorrent packages; `--smoke-test` passes; frozen exe answers the update, Windows integration and dashboard routes |
| Import gate | every module under `src/opengrab` is imported by the running app (`tools/import_audit.py`, no known orphans) |

## Milestones

| Milestone | State | Evidence |
|---|---|---|
| M2 CLI, M3 API, M4 GUI shell, M5 UI panels, M6 onedir | Done | Earlier tests plus the packaged smoke test (download, SHA-256, 7z) |
| M7 plugins, M8 captchas, M9 Cloudflare/clearance, M10 accounts, M11 torrents/post-processing | Done as modules, wired by W1-W12 | See wiring below |
| Integration audit (2026-10-30) | Done | Found built modules that the running app never imported; all are now wired, and the import gate test keeps it that way |
| W1 notifications, W2 captcha flow, W3-W5 flow control (schedule, speed profiles, data cap, per-site limits, queues) | Done | Feature tests against a real ApiServer and local file sites |
| W6 dynamic segmentation | Done | Slow range split while running, file hash checked; 8 connections at least 1.5x faster than 1 (best of two runs) |
| W7 proxies and logins | Done for HTTP/SOCKS proxies, Basic and Digest | Local forward proxy and auth fixtures; NTLM/Negotiate unverified |
| W8 LinkGrabber, Click'n'Load, spider | Done | Tests; Click'n'Load AES decoding against fixtures |
| W9 accounts in the engine | Done | Quotas, cookies, debrid; secrets masked and DPAPI-protected |
| W10 post-processing | Done for zip/7z, hash checks, virus scan | RAR unverified |
| W11 BitTorrent add-on | Done | Byte-exact download between two local libtorrent sessions; frozen exe with the add-on installed |
| W12 plugin sandbox, updates, signed marketplace | Done as policy and client | Ed25519 verification tests; no hosted catalog; no QuickJS runtime |
| Classic UI (IDM / JDownloader 2 look) | Done | Live browser checks, light and dark |
| M13 automation | Done | Event programs, signed webhooks, Folder Watch, keep-awake, when-done, CLI client |
| M14a appearance, M14b English + Simplified Chinese, first-run setup, accessibility | Done | i18n coverage test, label and keyboard tests, live checks in Chinese |
| Site plugins (2026-10-30) | Done, mostly offline-tested | 28 host, paste and tracker plugins plus FTP; live-verified: MediaFire, Mega single files, chomikuj up to the captcha, GoFile listing; the rest are tested against fixtures because no legitimate public test files were found. A failing plugin now hands the link to the next one (fixed known bug B1) |
| VirusTotal check | Done | Hash lookup, opt-in upload, quarantine and notification tested against a fake VirusTotal API; not yet run against the real service |
| M14c JDownloader 2 import, dashboard, link handlers, autostart | Done | Synthetic JD2 folder test; read-only detection of a real JD2 install; dashboard counted a real download; registry round trip in a scratch key |
| M14d updates, installer, CI matrix, Docker | Done; first signed release pending | Updater tests run the real swap and rollback script; update check works against GitHub; installer and Docker verified in CI only; release key created and trusted; an install-update-rollback round trip with real releases is still to do |

## What still needs the owner

See [project-spec.md](project-spec.md) section 9. In short: create the release signing key and
GitHub secret, decide on code signing, delta updates and DLC/CCF, and host a
plugin catalog if wanted.
