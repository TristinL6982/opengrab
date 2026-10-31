# Security policy

## Supported versions

Security fixes go into the latest release. Please update before reporting; the in-app updater (Settings, About) does it in one click.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub instead: open the repository's **Security** tab and choose **Report a vulnerability**. Include what an attacker can do, the steps to reproduce it, and the version you tested.

You can expect:

- an acknowledgement within 3 days,
- an assessment and a plan within 10 days,
- a fix in a release, and credit in the release notes if you want it.

## What is in scope

OpenGrab runs a local web server and handles files from the internet, so these matter most:

- reaching the API without the token, or with a paired device's token doing more than it should,
- a web page, a downloaded file, an archive or a plugin running code or writing outside the download folder,
- leaking the API token, account passwords, cookies or API keys,
- the updater installing something that is not a signed OpenGrab release,
- bypassing the rule that settings which run programs can only be changed on the computer itself.

Problems in third-party sites, and a site blocking OpenGrab, are not security issues: please open a normal issue for those.

## How OpenGrab protects you

- The API listens on `127.0.0.1` only and requires a token on every request. Other machines need a paired device token, over HTTPS unless you allow plain HTTP yourself.
- Paired devices can manage downloads but cannot change settings.
- Archives are extracted with checks against path traversal, links and decompression bombs.
- Updates are installed only when their Ed25519 signature matches the release key built into the app, and each update passes a self-test before it replaces the running version.
- Account passwords are encrypted with Windows DPAPI and never sent back to the UI.
