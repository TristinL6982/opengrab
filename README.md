# OpenGrab

OpenGrab is a local-first link grabber and resumable downloader with a web UI,
plugin hooks, CAPTCHA workflows, and a `yt-dlp` bridge. It is an early
development build, not a JDownloader replacement yet. See the implementation
gaps at the end before relying on it for unattended downloads.

## Requirements and quick start

- Python 3.12 or newer for the source install.
- Windows desktop mode needs the Microsoft WebView2 Runtime. Linux and macOS
  need a pywebview-supported web engine; the tray backend depends on the OS.
- Use a virtual environment, then run `python -m pip install -e .`.
- Start the desktop UI with `python -m opengrab` or the API/web UI with
  `python -m opengrab --headless`.
- Open `http://127.0.0.1:5678/`. The server binds to loopback by default.
- Check the install with `python -m opengrab --smoke-test`; it performs a real
  small download and checks the known size and SHA-256.

Useful CLI options: `--gui`, `--headless`, `--port`, `--bind`, `--data-dir`,
repeatable `--grab URL`, `--list-plugins`, `--version`, `--help`.

## Configuration and storage

Settings and the queue live under `%APPDATA%/OpenGrab` on Windows,
`~/Library/Application Support/OpenGrab` on macOS, or
`$XDG_DATA_HOME/OpenGrab` (default `~/.local/share/OpenGrab`) on Linux.
Downloads default to `~/Downloads`. Configure the download folder and
concurrency in Settings.

Environment overrides:

| Variable | Purpose |
|---|---|
| `OPENGRAB_DATA_DIR` | Settings, queue, and user plugins directory |
| `OPENGRAB_DOWNLOAD_DIR` | Download directory |
| `OPENGRAB_BIND` / `OPENGRAB_API_HOST` | Bind address; default `127.0.0.1` |
| `OPENGRAB_PORT` / `OPENGRAB_API_PORT` | Listen port; default `5678` |
| `OPENGRAB_BASE_PATH` | Reverse-proxy URL prefix such as `/opengrab` |
| `TZ` | Container timezone setting |

The GUI has a tray menu for showing the window and enabling/disabling copied
link monitoring. Clipboard links go to LinkGrabber for review; they are never
queued automatically by the watcher. The UI also provides download-root
scoped file operations, notifications, audio playback, and an opt-in web
terminal. Terminal commands run with the OpenGrab process permissions, and
require both the Settings toggle and the API bearer token.

ZIP/CBZ, 7z, and TAR-family files can be extracted after download. Extraction
rejects traversal paths, links, excessive file counts, expanded sizes above
the configured limit, and extreme ZIP compression ratios; it writes to a new
folder and preserves the source archive. Encrypted ZIP/7z/RAR password lookup is
off unless you set a local password-list file in Settings. That file is read
as UTF-8, with a 1 MiB / 1,000-entry limit; keep it private. RAR uses the
`rarfile` adapter and needs a compatible `unrar`, `unar`, or `bsdtar` program on `PATH`;
that external tool is not bundled, and RAR extraction has not yet been verified
against a RAR fixture.

## Remote access and security

Keep the default loopback binding for local use. Remote clients require the
owner API token and HTTPS. Put OpenGrab behind a trusted HTTPS reverse proxy;
the proxy must send `X-Forwarded-Proto: https` and should restrict access to
trusted users. For proxied remote requests, send `Authorization: Bearer <token>`.
The generated token is stored in `settings.json`; protect that file and rotate
the token there if it is exposed. Do not forward plain HTTP from an internet
facing proxy. `OPENGRAB_BASE_PATH` must match the proxy prefix. OpenGrab does
not currently configure direct TLS itself or provide multi-user accounts.

## Docker

Docker deployment files are included, but Docker is unavailable in the
development environment, so the image and compose setup have not been built or
run here. Review the files before exposing a service.

1. Copy `.env.example` to `.env`; choose the host `OPENGRAB_PORT`, and set
   `OPENGRAB_BASE_PATH` only when the HTTPS proxy uses that URL prefix.
2. Run `docker compose up -d --build`.
3. Persist `./config` and `./downloads`; the generated API token is in
   `./config/settings.json`.
4. Serve remote access only through an authenticated HTTPS proxy.

The Compose file publishes port 5678 on the host. For a private LAN, firewall
that port to trusted clients; direct remote HTTP is rejected by the app. There
is no GPU acceleration requirement for downloading. Synology, unRAID, UID/GID
mapping, and vendor-specific container steps are not yet verified or
documented.

## Plugins and media

Built-in plugins include direct HTTP links, generic HTML page extraction, and
the `yt-dlp` extractor bridge. Add custom Python plugins to the user `plugins`
folder and use **rescan plugins**. Treat plugins as executable code running
with OpenGrab's privileges; install only code you trust. The extractor bridge
does not imply that every upstream site works. Playlist and channel URLs are
enumerated as reviewable child links (10,000 by default, configurable up to
50,000); nothing is queued until individual children are selected or “queue
all” is chosen. Media downloads use the best progressive stream by default,
with an optional maximum resolution. Separate video/audio streams require
merging and currently fail with a clear message rather than silently dropping
audio. Playlist enumeration and format choice have fixture tests; live
extractor compatibility is not independently verified.

## Packagizer

Add rules in the Packagizer tab. Rules are evaluated in order and the first
matching URL/filename glob or regex decides the rename and/or relative target
subfolder. Preview shows the selected rule, path, and whether a collision
already exists. Templates support `{name}`, `{stem}`, `{ext}`, and `{host}`.
Rule paths and filenames are sanitized and confined to the download directory;
existing downloads are preserved and new collisions receive numbered names.

## Current limitations

- RAR extraction has an adapter but requires an external tool and is unverified;
  router reconnect is not implemented.
- The requested maintained inventories of 110+ hosters, 300+ decryptors, and
  1,400+ router models are not present. Upstream extractor count is not
  compatibility evidence.
- Browser extensions, a remote-control cloud service, diagnostics export,
  and Linux/macOS packaged desktop builds are not implemented/verified.
- The Windows onedir executable passes `--version`, plugin discovery, and the
  real-download smoke test. GUI lifecycle is mock-tested; WebView2/tray behavior
  still needs a real interactive smoke test. The executable is under
  `build/release/OpenGrab/` after a build.
- Docker is unverified. Do not treat the current build as production-ready.

See `PROJECT_SPEC.md`, `PROJECT_STATUS.md`, and `PROGRESS.md` for requirements,
decisions, and evidence. Keep the download directory backed up: interrupted
downloads retain `.part` files for resumption, but this prototype does not yet
provide all archival/recovery guarantees in the product specification.
