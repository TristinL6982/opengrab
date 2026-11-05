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

## Latest verification (2026-10-31, v0.1.3)

| Check | Result |
|---|---|
| Full test suite, Windows 11 | 1186 passed, no xfail (B1 fixed) |
| UI click-through | every view, toolbar button and dialog driven in a browser against a test instance; 85 settings saved and reloaded; language switch and add-on install checked |
| Plugin review | security and robustness fixes for the site plugins, each with a test in `tests/regression/test_plugin_review.py` |
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
| Site plugin review (2026-10-31) | Done for the issues found | Reviewed the new plugins and engine hooks for secrets, local-network redirects, waits, loops and crashes; fixes in Mega waits, `_hostflow.probe_direct`, dlproxy, pastes, PrivateBin, MediaFire, 1fichier, Rootz, DataNodes, FuckingFast, FileDitch, tapochek, Turbobit/Hitfile, VikingFile, FileKeeper, FTP child links, browser page lock and the log file. Each has a test in `tests/regression/test_plugin_review.py` that fails (or hangs) on the old code. Offline only; the engine-wide local-address rule and FTP logins are open decisions in the spec |
| Shared XFileSharing flow (2026-11-05) | Done, offline-tested | `plugins/builtin/_xfs.py` walks the download1/download2 steps every XFileSharing host shares (forms, countdowns, Turnstile/hCaptcha/reCAPTCHA, image and CSS-digit captchas, packed-JS links, limit/premium/offline messages). A site's quirks are a small `Profile`; the generic plugin sends unknown XFS sites to it. DataNodes now runs on it, and DataNodes and FuckingFast were corrected against the request order in a real JDownloader 2 log and the current JDownloader 2 plugins (referer, first form, `dl_token`/`g_captch__a`, escaped Turnstile widget, Turnstile key loaded by script, links on any host). The HTTP client keeps cookies set on redirect hops and merges a plugin's own Cookie header. Tests in `tests/regression/test_xfs_engine.py` (8 fail on the old code). Not yet tried against the live sites |
| Live-test fixes (2026-11-05) | Done, offline-tested; parts live-checked | Found while the owner used the test build. Captchas in a site's form are drawn in a real Edge on the site's own address (robots.txt rewritten), and their token goes to the plugin. Edge starts off-screen and comes forward only if a click is needed after 8 s; it is on by default, with a Settings checkbox. Live-checked with Cloudflare's test keys: always-pass returned a token in about 4 s, and forced-interactive came forward at 8 s. "Verify all" and an engine batcher solve every waiting link of a host in one Edge page, one token per link, one browser at a time (live: 5 tokens in 3.8 s). Clipboard watching is on by default, with a switch on the link grabber page. PrivateBin attachments are read. Remove asks list-only or delete files (Recycle Bin, download folder only). Settings has a search box and error rows link to the setting that fixes them. Local .torrent files work from file:// links and are kept in the data dir. Default-apps registration and a button that opens Windows' Default apps page for magnet/.torrent (UserChoice is never written). Torrent stats, a Peers dialog and an offline DB-IP country lookup (the download URL was checked; a real install is not yet done). Tests: `test_widget_in_browser`, `test_captcha_batch`, `test_clipboard_capture`, `test_sites_privatebin`, `test_remove_delete_files`, `test_settings_search_jump`, `test_torrent_local_file`, `test_default_apps`, `test_torrent_stats_peers`. Not yet seen in the owner's app: the delete dialog, the settings search in Chinese, peer flags on a real swarm, 30 widgets against a real sitekey. .torrent association fix: the Default apps button registered `OpenGrab.torrent` but `.torrent` still pointed at the installer's `OpenGrab.Download`, so the installed copy opened files; `.torrent` default/OpenWithProgids now use `OpenGrab.torrent` (installer.iss agrees), commands come from `launch_command()` with --data-dir/--port, no UserChoice written: `test_torrent_default_points_at_the_registered_progid`, `test_registered_commands_carry_this_copys_data_dir_and_port`, `test_registration_never_writes_a_user_choice_key` |
| VirusTotal check | Done | Hash lookup, opt-in upload, quarantine and notification tested against a fake VirusTotal API; not yet run against the real service |
| M14c JDownloader 2 import, dashboard, link handlers, autostart | Done | Synthetic JD2 folder test; read-only detection of a real JD2 install; dashboard counted a real download; registry round trip in a scratch key |
| M14d updates, installer, CI matrix, Docker | Done; first signed release pending | Updater tests run the real swap and rollback script; update check works against GitHub; installer and Docker verified in CI only; release key created and trusted; an install-update-rollback round trip with real releases is still to do |

## What still needs the owner

See [project-spec.md](project-spec.md) section 9. In short: create the release signing key and
GitHub secret, decide on code signing, delta updates and DLC/CCF, and host a
plugin catalog if wanted.
