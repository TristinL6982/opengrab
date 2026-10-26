# OpenGrab Product Specification

## Purpose and authority

This document records the intended product requirements for OpenGrab. It is the
source of truth for product scope. `AGENTS.md` directs coding agents to read
this document and `PROGRESS.md` before making changes. `PROGRESS.md` tracks
implementation status; a feature is not complete merely because it is listed
here or has a partial implementation.

When requirements are ambiguous, keep them visible in **Open decisions** and
ask the user rather than silently narrowing or changing them.

## Product goal

Build a fully fledged, unpaywalled internet download manager inspired by
JDownloader 2. It should capture and analyze links, resolve supported hosts and
media, manage reliable concurrent downloads, automate supported host workflows,
and provide a usable local and remote control interface. The product is
intended to exceed a basic downloader with useful automation, diagnostics,
recovery, and safety features.

The current repository describes a Python 3.12 backend, web UI, a Windows
desktop build, and a Docker deployment. The full platform requirement below is
broader than that current implementation plan and must be resolved before
claiming cross-platform support.

## Core requirements

These requirements come from the user's requested core feature list. They are
product requirements, not claims that the current code already implements them.

### 1. Platform independence

- Run on Windows, Linux, and macOS, as well as the user's stated
  "Java-compatible systems" target.
- Provide a consistent core feature set across supported platforms.
- Document platform-specific limitations, installation, updates, and required
  system dependencies.

### 2. LinkGrabber and clipboard capture

- Monitor copied links and identify downloadable files, archives, and media
  streams.
- Allow users to review, filter, and add detected links to the download queue.
- Support batch capture and avoid silently adding unwanted or duplicate links.

### 3. Concurrent and segmented downloads

- Download multiple files concurrently.
- Split eligible downloads into parallel chunks to use available bandwidth.
- Resume interrupted downloads and fall back safely when a server does not
  support byte ranges or parallel connections.
- Allow users to control concurrency and pause/resume individual items and
  queues.

### 4. Automatic archive extraction

- Automatically extract supported archives, including RAR, ZIP, and 7z.
- For password-protected archives, support user-authorized password lookup or
  search when configured.
- Report extraction failures and preserve the original archive unless the user
  has configured otherwise.

### 5. Host and decrypt plugins

- Provide an extensible plugin system for file hosters and link decryptors.
- Target support for more than 110 file hosters and more than 300 decrypt
  plugins, as requested by the user.
- Show which plugin handled a link, its compatibility/status, and useful failure
  details.

### 6. YouTube and media

- Support batch media downloads, including full YouTube playlists and channels.
- Make format and quality selection understandable and configurable.
- Clearly distinguish a single media item from a playlist/channel batch before
  starting a large download.

### 7. Bandwidth and queue control

- Provide configurable bandwidth limits.
- Support pausing/resuming transfers and queues at any time.
- Support concurrent-download and per-download connection limits.

### 8. CAPTCHA and host wait automation

- Include OCR-assisted CAPTCHA solving and a manual fallback.
- Handle host-imposed countdowns/wait times where supported by the relevant
  plugin.
- Surface the current state, wait time, and required user action in the UI.

### 9. Reconnect support

- Support user-configured router reconnect scripts to obtain a new IP where
  the router and network permit it.
- Target the user's requested breadth of more than 1,400 router models, with
  the supported method and compatibility documented per device.
- Make reconnect behavior explicit and user-controlled; do not promise that a
  reconnect bypasses a host's terms or network restrictions.

### 10. Packagizer rules

- Let users define rules to rename, sort, and move downloads based on attributes
  such as file type and source.
- Preview or explain which rules match a link and what actions will be applied.
- Avoid overwriting existing files without an explicit policy.

### 11. Web interface and remote control

- Provide a web UI to manage links, packages, queues, settings, and supported
  automation features.
- Support remote control through an integrated web interface and/or a defined
  remote service/API equivalent to the user's MyJDownloader requirement.
- Require authentication for remote access and document secure deployment.

## Quality and safety requirements

These additions improve reliability and make the core features safer to use.

### Reliability and recovery — must have

- Persist queue state and recover it after application or system restart.
- Verify downloads with available checksums and report when integrity cannot be
  verified.
- Record retry history and give actionable failure reasons.
- Resume safely after network interruption; use a single connection when range
  requests are unsupported or unreliable.
- Provide an exportable diagnostics report that redacts credentials, cookies,
  tokens, and other secrets.

### Archive and plugin safety — must have

- Protect extraction from path traversal, excessive expanded size, excessive
  file counts, and other archive bombs.
- Make password-list searching opt-in and configurable; disclose its source and
  limits.
- Document plugin permissions and compatibility. Make plugin failures visible
  and allow users to disable or remove a plugin.
- Do not execute downloaded or extracted content automatically.

### Remote access safety — must have

- Keep remote access authenticated and provide HTTPS/reverse-proxy guidance.
- Bind to localhost by default unless the user explicitly configures remote
  access.
- Keep powerful administrative features, especially a remote terminal,
  disabled unless explicitly enabled and authenticated.
- Explain which interfaces are exposed and how to revoke remote access.

## Additional enhancements roadmap

These were suggested as improvements beyond the stated core requirements. They
are desirable product enhancements and should be prioritized after the core
requirements unless the user changes priority.

### Next priority

- Browser extensions or a local browser-capture helper for sending links and
  selected media to OpenGrab.
- Duplicate detection and automatic grouping of related links into packages.
- Schedules, bandwidth profiles, per-host limits, and automatic pause/resume
  rules.
- Queue priorities and package dependency/order controls.
- A diagnostics view showing selected plugin, server range support, retries,
  and recent errors.
- Storage-space and configuration checks before starting large downloads.

### Later enhancements

- Connection testing and estimates for whether parallel chunks are likely to
  help a particular source.
- Notification controls and configurable completion actions.
- A versioned plugin compatibility catalog and plugin update workflow.
- Import/export for settings, rules, and queue lists.

## Acceptance criteria and evidence

Each requirement must have a testable acceptance criterion and recorded
verification before it can be marked complete in `PROGRESS.md`. At minimum:

- Platform claims must be verified by installing and launching on each claimed
  operating system and documenting any unavailable features.
- Download engine behavior must be checked for parallel-capable and
  non-parallel servers, interruption/resume, pause/resume, and integrity checks.
- Each plugin family must have maintained compatibility evidence; plugin
  counts alone do not establish that plugins work.
- Playlist/channel downloads must be checked for batch enumeration, user
  selection, and correct queue behavior.
- Archive handling must be checked for supported formats, password prompts,
  safety limits, and malicious path entries.
- Remote access must be checked for authentication, secure configuration, and
  disabled-by-default administrative capabilities.
- Packagizer rules must be checked for deterministic matching, preview, and
  collision handling.

Do not claim a numerical compatibility target (hosters, decryptors, routers)
unless the project has a reproducible inventory and a maintenance approach.

## Open decisions

The agent must keep these unresolved until the user answers or project evidence
supports a clear interpretation:

1. What does "Java-compatible systems" mean for this Python project: any OS
   capable of running Java, a Java-based integration, or a requirement to
   implement/package a Java application?
2. Does platform independence require native desktop builds on Windows, Linux,
   and macOS, or is a browser UI plus Docker sufficient on some platforms?
3. Are the targets of 110+ hosters, 300+ decrypt plugins, and 1,400+ routers
   strict launch requirements, long-term targets, or compatibility goals met
   through third-party/plugin ecosystems?
4. Which archive password sources and search limits are acceptable, and what
   licensing/source constraints apply to them?
5. Which browser extension platforms and remote-control service/API are
   required for launch?
6. What functionality is included in the user's "and more" request beyond the
   explicit requirements and the enhancement roadmap above?

