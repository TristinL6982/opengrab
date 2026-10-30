# Integration audit: modules with tests but no runtime caller

Date: 2026-10-30. Branch audited: `master` at `e6f5df0` (Hermes's M12k, later replaced by `08359ff`).
Scope: every `.py` under `src/opengrab` that no runtime entry point imports.
Product code was not changed for this audit; only `tools/import_audit.py` and
this document were added.

## 1. Method and result

`tools/import_audit.py` parses every module under `src/opengrab` with `ast`
(reading with `utf-8-sig`, because `logs.py` and `webview2.py` start with a
BOM), resolves absolute and relative intra-package imports (including imports
inside functions, which this code base uses for lazy loading), and walks the
graph from the five entry points `app`, `server`, `cli`, `smoke` and
`__main__`. Modules under `plugins/builtin/` are seeded as reachable because
`plugins/registry.py` loads them with `importlib`. No other dynamic import of a
package module exists (`grep importlib|__import__` finds only `registry.py`).

```bash
.venv\Scripts\python.exe tools\import_audit.py
```

Result on this tree:

| Modules under `src/opengrab` | Reachable from an entry point | Unreachable |
|---|---|---|
| 92 | 57 | 35 |

The 35 unreachable modules are listed in section 2 and the raw script output
is in Appendix A. They total about 3,700 lines of product code and 360
collected tests (every one of them passes), and none of them is bundled into
`OpenGrab.exe`
except the five under `plugins/` (the spec uses `collect_submodules("opengrab.plugins")`).

Four of the 35 are imported only by other orphans (`cnl` by `cnl_server`,
`hostlimits` by `proxies`, `plugins.permissions` by `plugins.sandbox` and
`plugins.marketplace`, and the `netconfig` cluster), so wiring one module in
can pull several others along.

## 2. Module inventory

Milestone letters come from the test file name and the commit that added the
module (`git log --oneline`, e.g. `feat(M11a): scheduler time windows`).

| Module | Milestone | Test file (tests/regression/) | Group |
|---|---|---|---|
| `accountauth` | M10c | `test_accountauth_m10.py` | Accounts |
| `archives` | M11q | `test_archives_m11.py` | Post-processing |
| `avscan` | M11h | `test_avscan_m11.py` | Post-processing |
| `captcha.harvest` | M6e | `test_captcha_harvest_m6.py` | Captcha |
| `captcha.inbox` | M6d, M12k | `test_captcha_inbox_m6.py`, `test_captcha_push_m12.py` | Captcha |
| `categories` | M11g | `test_categories_m11.py` | Queue |
| `challenge` | M6b | `test_challenge_m6.py` | Captcha |
| `cnl` | M8f | `test_cnl_m8.py` | LinkGrabber |
| `cnl_server` | M8h | `test_cnl_server_m8.py` | LinkGrabber |
| `cookies` | M10b | `test_cookies_m10.py` | Accounts |
| `debrid` | M10d | `test_debrid_m10.py` | Accounts |
| `hashverify` | M11e | `test_hashverify_m11.py` | Integrity |
| `hostlimits` | M11b | `test_hostlimits_m11.py` | Queue |
| `linkcheck` | M8c | `test_linkcheck_m8.py` | LinkGrabber |
| `netconfig` | M11r | `test_netconfig_m11.py` | Glue |
| `notifications` | M11f, M12k | `test_notifications_m11.py` | Notifications |
| `plugins.marketplace` | M7r | `test_plugin_marketplace_m7.py` | Plugins |
| `plugins.permissions` | M7p | `test_plugin_permissions_m7.py` | Plugins |
| `plugins.sandbox` | M7q | `test_plugin_sandbox_m7.py` | Plugins |
| `plugins.sdk` | M7j | `test_plugin_sdk_m7.py` | Plugins |
| `plugins.updates` | M7o | `test_plugin_updates_m7.py` | Plugins |
| `portmap` | M11k | `test_portmap_m11.py` | Torrent |
| `premium` | M10e | `test_premium_m10.py` | Accounts |
| `proxies` | M11c | `test_proxies_m11.py` | Network |
| `queues` | M11m | `test_queues_m11.py` | Queue |
| `reconnect` | M11d | `test_reconnect_m11.py` | Network |
| `recovery` | M11p | `test_recovery_m11.py` | Integrity |
| `scheduler` | M11a | `test_scheduler_m11.py` | Queue |
| `segments` | M11n | `test_segments_m11.py` | Integrity |
| `siteauth` | M11l | `test_siteauth_m11.py` | Network |
| `speedprofiles` | M11i | `test_speedprofiles_m11.py` | Queue |
| `spider` | M8d, M8e | `test_spider_m8.py`, `test_spider_robots_m8.py` | LinkGrabber |
| `torrent` | M11o | `test_torrent_m11.py` | Torrent |
| `trackers` | M11j | `test_trackers_m11.py` | Torrent |
| `usage` | M10a | `test_usage_m10.py` | Accounts |

Reachable modules that look related but are **not** orphans: `clearance`
(M6c, imported by `browser_solver`), `browser_solver`, `cdp`, `captcha.audio`,
`containers`, `grabber`, `linkgrab`, `packages`, `extrules`, `access`,
`remote`, `pairing`, `sessiondb`, `engine/archives`, `engine/packagizer`, all
of `media/`, `plugins.declarative` and `plugins.dirindex`.

## 3. Live code paths the orphans must hook into

These are the places that exist today and that the per-module notes refer to.

- **Bootstrap**: `app.py:77 start_server()` builds `DownloadManager(settings)`
  then `ApiServer(settings, manager)`; `run_gui_mode()` (line 115) adds the
  pystray tray menu (lines 247-250) and the clipboard watcher;
  `run_headless_mode()` (line 294) is the Docker path; `_close_server()`
  (line 110) is the only shutdown hook.
- **Grab phase**: `engine/manager.py:215 _grab_and_queue()`; the
  `except CaptchaRequired` block at line 232 sets `LinkStatus.CAPTCHA`,
  `captcha_kind`, `captcha_sitekey` and `captcha_site_url` on the Link.
  `_apply_grab()` (line 260) does the same from `GrabResult.needs_captcha`.
  `submit_captcha()` (line 475) re-runs the plugin with `ctx.manual_code`.
- **Resolve phase**: `_resolve()` (line 331) calls `plugin.resolve()` and
  records plugin health. `PluginContext.account_for()`
  (`plugins/base.py:149`) is the only account lookup; it uses
  `AccountManager.for_domain()`.
- **Worker loop**: `_worker()` (line 559) pops `self._ready` (a
  `PriorityQueue` keyed by `(-priority, created)`) and gates only on
  `settings.max_parallel`. `_run_download()` (line 575) plans the destination
  with `engine/packagizer.plan_packagizer()`, builds `LinkDownload` with the
  shared `self._global_limiter`, runs the transfer, merges media, then runs
  `engine/archives.extract_archive()` when `auto_extract_archives` is on
  (line 650), and finally sets `FINISHED` and broadcasts.
- **Retry**: `_fail()` (line 831) retries transient errors up to
  `settings.retry_max` with `sleep(min(2 ** n, 60))`.
- **Progress**: `_on_tick()` (line 852) copies bytes and speed from
  `LinkDownload.progress()` and broadcasts `progress`.
- **Downloader**: `engine/downloader.py:109 _open_session()` reads
  `settings.proxy` and hard-codes `impersonate="chrome"`; `_probe()`
  (line 268) does HEAD or a `Range: bytes=0-0` GET and returns size,
  accept-ranges and headers; `run()` (line 343) splits the file into a fixed
  `per_link_chunks` number of ranges (capped at 8, only for files of 4 MiB or
  more) and stores them in the `.part.json` sidecar via `_save_meta()`;
  `_stream()` (line 427) is the per-range fetch loop with its own retry;
  `_parallel()` (line 566) runs the range workers.
- **HTTP client**: `http_client.py:143 request()` and `stream()` (line 217)
  pick `proxy = settings.proxy or None`, keep a per-domain cookie jar
  (`_cookies_for`, line 115) and never inspect the body for challenges.
- **Captcha chain**: `captcha/chain.py SolverChain.solve()`; `ManualSolver`
  (`captcha/base.py:85`) returns the sentinel `__MANUAL__`, which
  `PluginContext.solve_captcha()` (`plugins/base.py:118`) turns into a
  `CaptchaRequired` exception. `BrowserCaptchaSolver` (line 479) needs a
  clearance store that nothing constructs.
- **Server**: `server.py:142` keeps `self._notifications = deque(maxlen=200)`
  and the `fanout()` closure (lines 153-186) appends an inline notice dict for
  `finished`, `error`, `captcha`, `login` and `paywall`; `GET
  /api/notifications` (line 997) and `POST /api/notifications/clear`
  (line 1179) serve it. `_api_get()` (line 931) and `_api_post()`
  (line 1055) are flat `if p == ...` dispatchers; every `/api/*` route needs
  the bearer token (`_authorized()`, line 422) and device scopes go through
  `_gate(action)` (line 432) on `AccessGate`. `POST /api/settings`
  (line 1234) only accepts keys that exist on `Settings` and are not in its
  forbidden set, so new settings keys become writable automatically.
  LinkGrabber review candidates live in `self._clipboard_candidates`
  (`capture_clipboard_text()`, line 191) and are served at
  `GET /api/linkgrabber`; pasted text goes through `POST /api/linkgrab/analyze`
  (`grabber.analyze_text`). M12f stores a feature's JSON config in the data
  dir (`extension_rules.json`) and exposes it at `/api/extension/config`,
  which is the pattern to copy for per-feature configs.
- **Settings**: `settings.py Settings` is a flat pydantic model with
  `validate_assignment=True`; the UI only edits the keys listed in
  `SET_FIELDS` (`ui/static/app.js:653`) with matching `#set-<key>` inputs in
  `index.html` (lines 199-245).
- **UI**: tabs are `queue`, `all`, `linkgrabber`, `accounts`, `filters`,
  `packagizer`, `settings`, `terminal`, `notifications`, `files`
  (`index.html:24-34`); parked captcha links get an inline text box from
  `renderInlineForms()` (`app.js:273`); SSE events are handled in `onEvent()`
  (`app.js:102`), which only knows link payloads and stats payloads.
- **Persistence**: `sessiondb.SessionDB` already stores one `queue` package
  with all links (`manager._save_now()`, line 881); `pairing.json`,
  `accounts.json` and `extension_rules.json` are separate files in the data
  dir.

## 4. Per-module findings

Each entry gives what the module provides, the settings keys it needs, the
API routes and UI it needs, the concrete hook point, and any duplication.
Settings keys are proposals; none of them exist yet unless marked.

### 4.1 Captcha and challenge (M6)

#### `challenge` (M6b)
- **Provides**: `detect_challenge(status, headers, body)` classifying
  Cloudflare JS and Turnstile interstitials, the 1020 block page, DDoS-Guard,
  reCAPTCHA, hCaptcha and standalone Turnstile, with sitekey and Ray ID.
  Standard library only. 15 tests.
- **Settings**: none new; reuses `captcha_browser_enabled`,
  `captcha_browser_engine`.
- **API / UI**: no new route. The parked-link row (`renderInlineForms`)
  should show the challenge kind and Ray ID and offer "Solve in browser" for
  CF kinds instead of a text box.
- **Hook**: `http_client.request()` and `stream()` after the response is
  read, and `downloader._probe()`: run `detect_challenge()`; when the kind is
  not `NONE`, raise `CaptchaRequired(kind=..., sitekey=..., site_url=url)`.
  `_grab_and_queue()` line 232 already turns that into `LinkStatus.CAPTCHA`.
  `generic.py:100` (`ctx.extract_captcha`) is the only plugin path that
  detects captchas today and it only finds image captchas.
- **Duplicates**: none. This is the missing first step of the M6 chain; today
  a Cloudflare page ends as `ERROR` or as a bogus direct download.

#### `captcha.inbox` (M6d, M12k)
- **Provides**: `CaptchaInbox`, a thread-safe list of pending human
  challenges with dedupe per host and kind, expiry, `solve`/`skip`,
  blocking `wait`, `purge`, and the `on_new(host, kind)` push hook added in
  M12k. 16 + 4 tests.
- **Settings**: `captcha_inbox_max_pending: int = 50`; reuse
  `captcha_manual_timeout_s` as the request timeout.
- **API / UI**: `GET /api/captcha/inbox` (pending requests), `POST
  /api/captcha/solve {id, answer}`, `POST /api/captcha/skip {id}`. UI: a
  "Captchas" badge on the header and a panel listing pending requests with
  the harvest widget (next entry) or image; replace the per-link text box for
  widget kinds.
- **Hook**: `DownloadManager.__init__` owns `self.captcha_inbox`;
  `_grab_and_queue()` line 232 and `_apply_grab()` line 260 call
  `inbox.submit(host, kind, site_url, sitekey)` and store the request id in
  `link.extra["captcha_request_id"]`; `submit_captcha()` line 475 calls
  `inbox.solve(id, code)` before re-running the plugin. `inbox.on_new` is set
  in `ApiServer.__init__` to push an SSE `captcha` event and (section 4.5) a
  notification. `on_new` is the push hook the M12k commit message describes as
  wired for paired devices; nothing calls it.
- **Duplicates**: overlaps with the Link fields `captcha_image`,
  `captcha_kind` and the `captcha_*` keys in `link.extra`. Keep the Link
  fields as the UI's view and make the inbox the owner of waiting state.

#### `captcha.harvest` (M6e)
- **Provides**: `build_harvest_page(kind, sitekey, request_id, submit_path)`
  rendering a minimal page that loads the reCAPTCHA, hCaptcha or Turnstile
  script and posts the token to `/api/captcha/solve`; `extract_token(kind,
  form)`. 15 tests.
- **Settings**: none.
- **API / UI**: `GET /api/captcha/harvest/{request_id}` serving the page
  (HTML, not JSON) for an `<iframe>` in the Captchas panel and for the
  browser solver window. Caveat: the page posts without a bearer token, and
  every `/api/*` route requires one (`_authorized()`), so
  `POST /api/captcha/solve` must accept the unguessable request id as its
  credential or the page must be served with the token cookie.
- **Hook**: new routes in `_api_get()`/`_api_post()`; the request id comes
  from `captcha.inbox`. The widget needs to run on the target site's origin
  for the token to be accepted by most sites, so this page is mainly useful
  inside the Edge CDP solver (`browser_solver.py`) or for sites that accept
  tokens from any origin.
- **Duplicates**: none.

### 4.2 Plugins (M7)

#### `plugins.sdk` (M7j)
- **Provides**: `validate_template(dict)`, `validate_template_file(path)` and
  `check_url(tpl, url)` for declarative JSON templates. 10 tests (including
  validation of the bundled `templates/example_host.json`).
- **Settings**: none.
- **API / UI**: `POST /api/plugins/validate {template}` and a CLI flag
  `opengrab --validate-template <file>` in `cli.py:_parser()`. UI: a text
  area under Settings > Plugin health that shows validation errors.
- **Hook**: `cli.py main()` before the mode switch; `registry._scan_templates()`
  (`plugins/registry.py:121`) already records loader errors per file and could
  report the same messages.
- **Duplicates**: `DeclarativePlugin.__init__` does the validation; the SDK
  is a thin wrapper. Low cost.

#### `plugins.updates` (M7o)
- **Provides**: `plan_updates(installed, index)`, `apply_update(entry, data,
  dest_dir)` (sha256 check, template validation, id match, atomic write) and
  `check_and_update(installed, index_url, fetch, dest_dir)`. 8 tests.
- **Settings**: `plugin_index_url: str = ""`, `plugin_auto_update: bool =
  False`, `plugin_update_interval_h: int = 24`.
- **API / UI**: `POST /api/plugins/update` returning `{updated, failed}`;
  Settings > Plugin health gets a "Check for template updates" button and a
  last-checked line.
- **Hook**: `DownloadManager.start()` after `registry.scan()` spawns the
  check when enabled; `fetch` is `HttpClient.get(url).content`; `dest_dir` is
  `user_plugins_dir()`; afterwards `registry.scan(force=True)`. Installed
  versions must be tracked in a small `templates_installed.json`, because
  `DeclarativePlugin` rejects a `version` key (`ALLOWED_KEYS` in
  `plugins/declarative.py`), so the version cannot live in the template.
- **Duplicates**: `version_tuple` is also used by `plugins.marketplace`.

#### `plugins.permissions` (M7p)
- **Provides**: `Permissions(hosts, files, cookies)` with `allows_url`,
  `check_*`, `parse_permissions`, `requested_beyond(requested, granted)` and
  `load_grants`/`save_grants` JSON persistence. 10 tests.
- **Settings**: none; grants live in `plugin_grants.json` in the data dir.
- **API / UI**: `GET /api/plugins/{id}/permissions` (requested vs granted) and
  `POST` to grant; Settings > Plugin health gets a Permissions column and an
  approve dialog listing what the plugin asks for.
- **Hook**: `registry._scan_user_dir()` (`plugins/registry.py:101`) reads a
  `PERMISSIONS` dict or `permissions.json` next to each user plugin; grants are
  loaded with `load_grants(app_data_dir() / "plugin_grants.json")` in
  `DownloadManager.__init__`.
- **Duplicates**: none.

#### `plugins.sandbox` (M7q)
- **Provides**: `GuardedClient` that checks every `request/get/post/head/
  stream` URL and cookie access against `Permissions`, and
  `sandbox_context(ctx, perms)`. 11 tests.
- **Settings**: `plugin_sandbox_enabled: bool = True`.
- **API / UI**: none beyond the permissions UI; denied calls should appear
  in plugin health (`registry.record_result(pid, False, "permission ...")`).
- **Hook**: `DownloadManager._ctx()` (line 196) returns a plain
  `PluginContext`; wrap it with `sandbox_context()` for plugins whose
  `origin` is `user` or `template`, leaving builtins unrestricted. Both
  `_grab_and_queue()` and the three `retry()` closures call `_ctx()`.
- **Duplicates / caveat**: this guards network and cookies only. The spec's
  "QuickJS or restricted subprocess" sandbox (section 6c) is not implemented;
  a Python plugin can still import anything. Record that gap in the spec.

#### `plugins.marketplace` (M7r)
- **Provides**: catalog parsing (`parse_catalog`, `Listing` with ratings and
  permissions), `search`, `updates_available` and `verify_download` with an
  injected signature verifier. 20 tests.
- **Settings**: `marketplace_url: str = ""`, `marketplace_require_signature:
  bool = True`.
- **API / UI**: `GET /api/marketplace?q=&sort=`, `POST
  /api/marketplace/install {id}`; a new Plugins tab with search, ratings and
  install buttons.
- **Hook**: `_api_get()`; install writes through `plugins.updates.apply_update`
  for templates or into `user_plugins_dir()` for scripts, then
  `registry.scan(force=True)`.
- **Duplicates / caveat**: there is no hosted catalog and no signature
  scheme (the verifier is injected and nothing implements one; `cryptography`
  is not a dependency). Defer until a catalog exists.

### 4.3 LinkGrabber (M8)

#### `linkcheck` (M8c)
- **Provides**: `check_link(url, head)` returning online/offline/unknown,
  size and Content-Disposition filename (RFC 5987 aware); `check_links`,
  `summarize`. 14 tests.
- **Settings**: `linkgrabber_online_check: bool = True`.
- **API / UI**: `POST /api/linkgrabber/check {urls}` or run automatically on
  analyze; LinkGrabber rows show an online/offline dot, size and name.
- **Hook**: `server.capture_clipboard_text()` (line 191) and `POST
  /api/linkgrab/analyze` (line 1160) after `grabber.analyze_text`; `head` is
  `lambda u: (r.status, r.headers)` over `HttpClient.request("HEAD", u)`,
  run in a background thread so the paste stays responsive.
- **Duplicates**: `plugins/builtin/direct.py` does its own HEAD sniff for the
  same headers; `linkcheck._filename_from_cd` duplicates
  `downloader._cd_filename`. Share one Content-Disposition parser.

#### `spider` (M8d, M8e)
- **Provides**: `crawl(start_url, fetch, SpiderOptions)` with depth limit,
  same-domain or include/exclude regexes, file-type filter, `max_pages`,
  robots.txt respect and a per-request delay; returns pages, files, errors
  and blocked URLs. 10 tests.
- **Settings**: `spider_max_pages: int = 100`, `spider_delay_s: float = 0.5`,
  `spider_respect_robots: bool = True`.
- **API / UI**: `POST /api/linkgrab/spider {url, depth, same_domain,
  extensions, include, exclude}` starting a background job and `GET
  /api/linkgrab/spider/{id}` for progress; LinkGrabber tab gets a "Spider a
  site" form; results go into the review list, never straight to the queue.
- **Hook**: new routes in `_api_post()`/`_api_get()`; `fetch` is
  `HttpClient.get(url)` returning `(content_type, text)`; run under
  `manager._spawn`; feed files into `self._clipboard_candidates` the way
  `capture_clipboard_text()` does. The extension's Download All (M12h) covers
  one page; the spider is the multi-page version required by spec 6b.
- **Duplicates**: `spider.extract_links` overlaps `plugins/dirindex.py` and
  `generic.py` link extraction.

#### `cnl` (M8f)
- **Provides**: Click'n'Load 2 decoding: `key_from_jk`, AES-CBC
  `decrypt_crypted`, `parse_links`, `decode_addcrypted2(form)`. 5 tests.
- **Settings**: none (see `cnl_server`).
- **Hook**: only through `cnl_server`.
- **Dependency**: imports `Cryptodome` (pycryptodomex), which is **not** in
  `requirements.txt`; it is present only as a transitive dependency of
  `py7zr`. Add it explicitly before wiring.

#### `cnl_server` (M8h)
- **Provides**: `handle_cnl(method, path, form, on_links)` implementing
  `/jdcheck.js`, `/flash/add` and `/flash/addcrypted2`, and `make_server(...)`
  returning a `ThreadingHTTPServer` on `127.0.0.1:9666`. 9 tests, three of
  them over a real socket.
- **Settings**: `cnl_enabled: bool = True`, `cnl_port: int = 9666`,
  `cnl_auto_add: bool = False`.
- **API / UI**: `GET /api/cnl/status` (listening, port, last error);
  Settings toggle and a status pill; LinkGrabber shows "from Click'n'Load"
  with the package name and passwords.
- **Hook**: `app.start_server()` (line 77) starts it in a daemon thread next
  to `ApiServer` and `_close_server()` (line 110) shuts it down; `on_links`
  feeds the review list (`server.capture_clipboard_text`-style) and stores
  `package` and `passwords` in `link.extra`. A busy port 9666 must be a
  warning, not a startup failure (the C7 lesson).
- **Security note**: the listener sends `Access-Control-Allow-Origin: *` by
  design (browser pages post to it). It must therefore never start downloads
  or change settings; adding to the review list only is the safe default.
- **Duplicates**: none; the REVIEW (section 3) marks Click'n'Load as missing
  even though this module exists.

### 4.4 Accounts, premium and debrid (M10)

#### `usage` (M10a)
- **Provides**: `UsageRule(daily_bytes, max_parallel, reset_hour_utc)` and
  `UsageTracker` counting bytes per account per quota day with
  `can_start`, `remaining`, dict round trip. 9 tests.
- **Settings**: none; rules live in `Account.data["usage"]`; tracker state in
  `usage.json` in the data dir.
- **API / UI**: Accounts tab shows today's usage and remaining quota; `POST
  /api/accounts` must accept a `data` dict (today it takes only site,
  username and password, `server.py:1256`).
- **Hook**: `_on_tick()` (line 852) records the byte delta for links whose
  `link.extra["account"]` is set; `accountauth.pick_account` consults it.

#### `cookies` (M10b)
- **Provides**: Netscape `cookies.txt` and `Cookie:` header parsing,
  `cookies_for_url(cookies, url, now)` with domain, path, secure and expiry
  rules, `to_header`. 10 tests.
- **Settings**: none; cookies live in `Account.data["cookies_txt"]` or
  `["cookie_header"]`.
- **API / UI**: Accounts tab gets "Import cookies.txt" (paste) per account.
- **Hook**: `PluginContext.account_for()` (`plugins/base.py:149`) and
  `_run_download()` headers (lines 632-633, where extension cookies are
  merged via `extrules.browser_request_extras`): add the account's cookie
  header.
- **Duplicates**: a third cookie handler next to `http_client._cookies_for`
  (per-domain jar) and `extrules.scope_cookies` (extension handoff). Fold the
  matching rules into one helper.

#### `accountauth` (M10c)
- **Provides**: glue over `usage` and `cookies`: `usage_rule(acc)`,
  `account_cookies(acc)`, `cookie_header_for(acc, url, now)`,
  `matches_domain`, `pick_account(accounts, domain, tracker, active)`
  (premium first, skipping accounts over quota or parallel limit). 8 tests.
- **Settings**: none.
- **Hook**: replace `AccountManager.for_domain()` in
  `PluginContext.account_for()` with `pick_account(...)`; the manager owns the
  `UsageTracker` and an `active` count per account updated in
  `_run_download()`.
- **Duplicates**: `matches_domain` duplicates `AccountManager.for_domain`'s
  suffix matching.

#### `debrid` (M10d)
- **Provides**: Real-Debrid and AllDebrid `unrestrict(link, token, http)` with
  an injected HTTP function, `service_for(acc)` (by `data["debrid"]` or site)
  and `unrestrict_with(acc, link, http)`. 6 tests.
- **Settings**: none; the token is `Account.data["api_token"]` or the
  password.
- **API / UI**: Accounts tab gets a "Debrid service" selector and token
  field; link rows show "via Real-Debrid".
- **Hook**: through `premium`.

#### `premium` (M10e)
- **Provides**: `premium_resolve(url, accounts, http, tracker, active)`
  returning a `ResolveResult` from the first debrid account whose service
  succeeds. 6 tests.
- **Settings**: `premium_prefer_debrid: bool = True`.
- **Hook**: `_resolve()` (line 331) before `plugin.resolve()` when a debrid
  account exists for the hoster, and `accept_paywall()` (line 533) as the
  retry path for `PAYWALL` links; `http` adapts `HttpClient.request`. Record
  `link.extra["account"]` and `["service"]` for the UI and the usage tracker.
- **Duplicates**: none; spec 6c "Premium plugin paths, debrid services" has no
  other implementation.

### 4.5 Queue, scheduler and network (M11)

#### `scheduler` (M11a)
- **Provides**: `Window(start, end, days, action, speed_kbps)` with overnight
  handling, `window_from_dict`, `active_window`, `downloads_allowed`,
  `speed_limit_kbps`. 9 tests.
- **Settings**: `scheduler_enabled: bool = False`, `scheduler_windows:
  list[dict]`.
- **API / UI**: windows ride on `/api/settings` (the whitelist accepts list
  fields); Settings gets a "Scheduler" table (start, end, days, action,
  speed) and the header shows "paused by scheduler" when a pause window is
  active.
- **Hook**: `_worker()` (line 559) checks `downloads_allowed()` before
  popping `_ready` and sleeps instead of starting; a one-per-minute tick
  (started in `start()`) applies `speed_limit_kbps()` through
  `_global_limiter.set_rate()`. A `pause` window must also pause active
  downloads (`pause_download`) and `resume` them when it ends, remembering
  which links the scheduler paused (`link.extra["note"]`).
- **Duplicates**: the effective speed limit then has three sources
  (`settings.speed_limit_kbps`, scheduler window, speed profile); add one
  `effective_limit()` resolver in the manager.

#### `hostlimits` (M11b)
- **Provides**: `HostLimits(default_max, per_host)` counting active
  downloads per host with `can_start`/`start`/`finish`; `host_of(url)`.
  7 tests.
- **Settings**: `max_per_host: int = 0`, `per_host_limits: dict[str, int]`.
- **API / UI**: via `/api/settings`; Settings fields; link rows show
  "waiting for host slot".
- **Hook**: `_worker()` before `_run_download()`: if `not
  host_limits.start(url)` re-queue with a short delay (`_ready_put` from a
  `_spawn`ed sleeper); `finish(url)` in the `finally` of `_run_download()`.
- **Duplicates**: `host_of` exists three times (`hostlimits`, `siteauth`,
  `extrules`).

#### `proxies` (M11c)
- **Provides**: `parse_proxy` for http, https, socks4, socks4a, socks5,
  socks5h with credentials, `host_matches`, and `ProxyList` with ordered
  proxies, per-host rules, a bypass list, failover (`mark_failed`,
  `mark_ok`) and `choose(url)`. 9 tests.
- **Settings**: `proxies: list[str]`, `proxy_rules: dict[str, str]`,
  `proxy_bypass: list[str]`; keep `proxy` (existing) as the single-proxy
  shortcut that seeds the list.
- **API / UI**: `POST /api/proxies/test {proxy}`; Settings "Proxies" list
  editor with rules and bypass; the queue shows which proxy a link used.
  PAC/WPAD and the system proxy from spec 6b are not implemented here.
- **Hook**: `http_client.request()` line 156 and `stream()` line 233
  (`proxy = s.proxy or None`) and `downloader._open_session()` line 109 take
  `proxy_list.choose(url).url`; connect errors in `_stream()` call
  `mark_failed()` and retry with the next candidate. curl_cffi accepts
  `socks5h://` URLs; the httpx fallback needs `httpx[socks]`.
- **Duplicates**: none.

#### `reconnect` (M11d)
- **Provides**: `ReconnectConfig`, `Reconnector(config, do_reconnect,
  get_ip)` with `should_reconnect(wait_seconds, active_downloads)`, retry
  until the IP changes, cooldown and history. 9 tests.
- **Settings**: `reconnect: dict` (enabled, min_wait_seconds,
  cooldown_seconds, max_attempts, `command` to run, `ip_check_url`).
- **API / UI**: `POST /api/reconnect` (manual), `GET /api/reconnect`
  (history); Settings section and a palette action "Reconnect now".
- **Hook**: `_fail()` (line 831) when a host answers with a wait or IP ban
  (429, or a plugin-reported wait time) and `len(self._active) == 0`;
  `do_reconnect` runs the user's command with `subprocess`; `get_ip` fetches
  `ip_check_url` through `HttpClient`.
- **Caveat**: needs a router script or a LiveHeader-style recipe to be
  useful; the README already says reconnect is not implemented. Low priority.

#### `hashverify` (M11e)
- **Provides**: `parse_expected("sha256:<hex>" or bare hex)`, chunked
  `hash_file`, `verify_file` returning `VerifyResult`, `parse_checksum_file`
  for `sha256sum`/`md5sum` output, and `bad_chunks(path, chunk_size,
  expected)`. CRC32, MD5, SHA-1/256/512. 8 tests.
- **Settings**: `verify_hash: bool` **already exists and is unused** (REVIEW
  B14); add `hash_checksum_files: bool = True` to look for `SHA256SUMS`,
  `.sha256`, `.md5`, `.sfv` next to the download.
- **API / UI**: `POST /api/links/{id}/verify {expected}`; LinkGrabber and the
  queue row get a checksum field and a "hash ok / mismatch" badge.
- **Hook**: `_run_download()` after `_finish_media()` and before the
  archive step (line 650): if `link.extra["expected_hash"]` is set (from the
  plugin's `GrabResult.extra`, the LinkGrabber, or a checksum file), call
  `verify_file`; on mismatch set `ERROR "hash mismatch"` or, with
  `recovery.repair_ranges` + `bad_chunks`, re-download only the bad ranges.
- **Duplicates**: none. `smoke.py` computes a SHA-256 of the fixture by hand
  and could use `hash_file`.

#### `notifications` (M11f, M12k)
- **Provides**: `NotificationCenter(max_items, clock, toaster,
  toasts_enabled)` with history, unread count, `mark_read`, `mark_all_read`,
  `clear`, JSON round trip, `notify_download(name, status, error)` and the
  M12k `captcha_waiting_notification(host, kind)`. 6 tests;
  `captcha_waiting_notification` has no test and no caller.
- **Settings**: `notifications_toasts: bool = True`, `notifications_max:
  int = 100`.
- **API / UI**: keep `GET /api/notifications` and `POST
  /api/notifications/clear`; add `POST /api/notifications/{id}/read` and
  `/read-all`; the Notifications tab needs an unread badge, and
  `renderNotifications()` (`app.js:396`) reads `n.message` and `n.timestamp`
  while `Notification` exposes `title`, `message` and `time`, so one side
  must change.
- **Hook**: replace the `deque` at `server.py:142` and the inline notice dict
  in `fanout()` (lines 157-172) with `center.notify_download(...)` and
  `center.add(...)`; the `toaster` is `pystray.Icon.notify(message, title)`
  from the tray icon created in `run_gui_mode()` (line 244), which gives the
  native Windows toast required by spec 6c; persist `to_list()` to
  `notifications.json` in the data dir on each add and load it in
  `ApiServer.__init__`. `captcha.inbox.on_new` calls
  `center.add(*captcha_waiting_notification(host, kind))`.
- **Duplicates**: the server deque is the live duplicate of this class.

#### `categories` (M11g)
- **Provides**: default Video, Music, Documents, Archives, Programs
  categories with extension rules (double suffixes such as `.tar.gz`
  handled), `CategoryRules.category_for`, `folder_for(filename, base_dir)`,
  list round trip. 8 tests.
- **Settings**: `categories_enabled: bool = True`, `categories: list[dict]`
  (`CategoryRules.to_list()`).
- **API / UI**: via `/api/settings`; Settings "Categories" table (name,
  extensions, folder); queue filter chips by category.
- **Hook**: `_run_download()` destination planning (lines 594-596): when
  `plan_packagizer()` returns no match (`plan["matched"]` falsy), use
  `folder_for(filename, root)` as the subdirectory; store
  `link.extra["category"]` for the UI.
- **Duplicates**: overlaps `engine/packagizer` subfolder rules; categories
  should be the default the Packagizer falls back to, not a second rule
  engine.

#### `avscan` (M11h)
- **Provides**: `ScanConfig` (off by default; Defender or a custom command
  with `{path}`), `build_command`, `interpret` (exit code to clean,
  infected, error), `Scanner.scan()` that never raises, history and
  `flagged()`. Runner and existence check are injected. 9 tests.
- **Settings**: `avscan: dict` = `ScanConfig.to_dict()` (enabled False,
  scanner, custom_command, infected_codes, timeout_s, quarantine).
- **API / UI**: `GET /api/avscan` (history and flagged files); link rows get
  a clean/infected/error badge; Settings section with a "Test scanner"
  button.
- **Hook**: `_run_download()` right after `FINISHED` is set (line 670): run
  `Scanner.scan(path)` via `_spawn` so the queue never waits (spec 6a), with
  `runner = subprocess.run(argv, timeout=...)`; store the `ScanResult` in
  `link.extra["avscan"]`; when `quarantine` is on and the result is infected,
  move the file to `download_dir/quarantine/` and raise a notification.
- **Duplicates**: none.

#### `speedprofiles` (M11i)
- **Provides**: `SpeedProfile` (download and upload bytes/s), `ProfileSet`
  with Normal, Gaming and Night defaults, switch and round trip; `DataCap`
  (daily or monthly, stop or slow) and `DataCapTracker` with history and
  `effective_limit`. 10 tests.
- **Settings**: `speed_profiles: dict` (`ProfileSet.to_dict()`),
  `data_cap: dict | None`; tracker usage persisted in `datacap.json`.
- **API / UI**: `GET/POST /api/speed-profile` (switch), `GET /api/datacap`;
  a profile dropdown in the header, a tray submenu "Speed profile" in
  `run_gui_mode()` (lines 247-250), and Settings editors for profiles and the
  cap. Scheduler windows should be able to name a profile.
- **Hook**: the active profile's `download_limit` and `effective_limit()`
  feed `_global_limiter.set_rate()`; `_on_tick()` (line 852) adds the byte
  delta to the `DataCapTracker`; `_worker()` checks `allows_download()`.
  Upload limits only matter once the torrent engine exists.
- **Duplicates**: third source of the speed limit (see `scheduler`).

#### `trackers` (M11j)
- **Provides**: tracker URL normalisation, public list parsing,
  `TrackerManager` with defaults, merge from a list, per-tracker announce
  health, `healthy_urls`, `for_torrent(own, extra, removed)`, round trip.
  8 tests.
- **Settings**: `trackers: dict` (`TrackerManager.to_dict()`),
  `tracker_list_url: str` (public list to merge daily).
- **API / UI**: `GET/POST/DELETE /api/trackers`; Settings "Trackers" table
  with health columns; per-torrent add/remove in the torrent row.
- **Hook**: `TorrentEngine.add_magnet(..., extra_trackers=
  trackers.for_torrent(magnet.trackers))`; announce results from the
  libtorrent alert loop call `record_announce`.
- **Duplicates**: none. Useless without `torrent`.

#### `portmap` (M11k)
- **Provides**: `PortMapConfig`, `PortMapper(config, clients, probe)` trying
  UPnP then NAT-PMP through injected clients, manual port, rollback on
  partial failure, `release()` and `reachable()`. 8 tests.
- **Settings**: `portmap: dict` (enabled, port, protocols, methods, manual).
- **API / UI**: `GET /api/portmap` (status), `POST /api/portmap/test`;
  Settings "Port mapping" with the reachability result.
- **Hook**: `DownloadManager.start()` maps once the torrent session exists
  and `shutdown()` releases; clients wrap `miniupnpc` and a NAT-PMP client
  (both optional dependencies, not in `requirements.txt`); the probe asks an
  external port-check service. Useless without `torrent`.

#### `siteauth` (M11l)
- **Provides**: `CredentialStore` of per-host-pattern credentials with
  injected protect/unprotect (DPAPI), `find` preferring exact over wildcard,
  separate server and proxy credentials, `pick_scheme` choosing the strongest
  of Negotiate, NTLM, Digest, Basic from `WWW-Authenticate`, and libcurl
  scheme names. 8 tests.
- **Settings**: `site_credentials: list[dict]` (`CredentialStore.to_list()`,
  secrets already protected).
- **API / UI**: `GET/POST/DELETE /api/site-credentials`; Accounts tab gets a
  "Server and proxy logins" section (host pattern, user, scheme, proxy flag).
- **Hook**: `http_client.request()`/`stream()` and `downloader._stream()`:
  on 401 or 407 call `pick_scheme(headers)` and `store.auth_for(url,
  for_proxy)`, then retry with curl_cffi `auth=(user, password)` and the
  matching `http_auth` option. `protect`/`unprotect` are
  `AccountManager._dpapi_protect/_dpapi_unprotect` (`accounts.py:89-93`).
  NTLM and Negotiate depend on the libcurl build shipped with curl_cffi
  having SSPI; verify on Windows before claiming them.
- **Duplicates**: a second credential store next to `AccountManager`
  (`accounts.json`); either store `Credential`s as `Account`s with
  `data["scheme"]`, or make `AccountManager` the persistence layer.

#### `queues` (M11m)
- **Provides**: `QueueManager` with named queues, per-queue `max_active`,
  pause, rename, remove (items move to Main), `move`, `runnable()` (FIFO per
  queue, skipping paused and full queues), dict round trip. 7 tests.
- **Settings**: `queues: dict` (`QueueManager.to_dict()`), or store it as a
  second package row in `SessionDB` next to the existing `queue` package.
- **API / UI**: `GET/POST /api/queues`, `POST /api/links/{id}/move {queue}`;
  the Queue tab gets queue tabs or a selector with per-queue pause and
  concurrency; "Add URLs" gets a queue picker.
- **Hook**: `add_url()` (line 163) assigns `link.extra["queue"]` and
  `queue_manager.add(link.id, queue)`; `_worker()` (line 559) takes
  `runnable()` instead of popping `_ready` blindly (or filters popped ids
  through it); `start`/`finish` in `_run_download()`; `remove_link()` and
  `clear_finished()` call `discard`.
- **Duplicates**: overlaps the single `_ready` priority queue and
  `max_parallel`; the Main queue's `max_active` should be `max_parallel`.

#### `segments` (M11n)
- **Provides**: `SegmentPlanner(size, max_connections, min_size, ranges)`
  with up to 16 connections, `next_segment()` that hands out planned ranges
  and then splits the remaining range of the slowest active segment
  (IDM-style), `report`, `release`, `downloaded`, `complete`. 7 tests.
- **Settings**: raise the `per_link_chunks` UI maximum from 8 to 16 and add
  `segment_min_mb: int = 1`.
- **API / UI**: Settings field; optional per-link segment bar in the queue
  row.
- **Hook**: `downloader.run()` lines 386-391 build a fixed list of
  `per_link_chunks` ranges and `_parallel()` (line 566) runs exactly those;
  replace both with a planner: each worker thread asks `next_segment()` when
  it finishes a range, and `_save_meta()`/`_load_meta()` persist the planner's
  segments (start, end, done) instead of the fixed triples. Keep the
  `RangeUnsupported` fallback to one stream.
- **Duplicates**: the static planner in `run()` is the live duplicate.
  Spec 6a asks for a benchmark against single-connection curl.

#### `torrent` (M11o)
- **Provides**: `parse_magnet`, `TorrentSettings` (DHT, PEX, LSD,
  sequential, ratio and seed-time limits), `file_priorities`,
  `should_stop_seeding`, and `TorrentEngine` over an injected session with
  `add_magnet`, `select_files`, `check_seeding`, `remove`. libtorrent is never
  imported. 7 tests.
- **Settings**: `torrent: dict` (`TorrentSettings` plus `listen_port`,
  `save_path`).
- **API / UI**: `/api/torrents`, `/api/torrents/{id}/files` (selection),
  `/api/torrents/{id}/sequential`; torrent rows with peers, ratio and a file
  picker; `magnet:` handler (spec 6c).
- **Hook**: `add_url()` (line 163): URLs starting with `magnet:` (and
  `.torrent` files from the LinkGrabber) bypass the plugin grab and go to
  `TorrentEngine`; a libtorrent `session` adapter implementing
  `apply_settings/add/remove` and an alert poll thread that maps status to
  Link progress and `FINISHED`. Dependency: `libtorrent` wheels (about 10-20
  MB, CPU only) as an optional extra; `smoke.py` must pass without it.
- **Duplicates**: none.

#### `recovery` (M11p)
- **Provides**: `RetryPolicy` (exponential backoff with cap, retryable
  status set), `Validators` from headers, `resume_headers` (`If-Range`),
  `can_resume`, `resume_decision` (resume, restart or refresh), and
  `repair_ranges` merging bad chunk indexes into byte ranges. 7 tests.
- **Settings**: `retry_policy: dict` (max_attempts, base_delay, factor,
  max_delay); map `retry_max` onto `max_attempts` to avoid two knobs.
- **API / UI**: none; queue row notes "restarted: file changed on server" or
  "link expired, refreshing".
- **Hook**: `downloader._probe()` (line 268) stores `validators_from_headers`
  in the `.part.json` sidecar via `_save_meta()`; on resume `_load_meta()`
  compares them with `can_resume`/`resume_decision` and restarts instead of
  trusting the `.part` file; `_stream()` sends `resume_headers()`; `refresh`
  re-runs `_resolve()` through the plugin (spec 6a "refresh expired links");
  `_fail()` line 841 replaces `min(2 ** n, 60)` with `policy.delay_for()`;
  `repair_ranges` pairs with `hashverify.bad_chunks`.
- **Duplicates**: `RetryPolicy` versus the two existing retry loops
  (`downloader._stream()` with `retry_max` and `manager._fail()`).

#### `archives` (M11q, top level)
- **Provides**: pure helpers: `archive_kind`, multi-volume detection
  (`volume_info`, `group_volumes`, `is_set_complete`, `first_volume` for
  `.part01.rar`, `.r00`, `.7z.001`), `safe_member_path`, `check_limits`,
  `load_passwords`, `password_candidates`, `seven_zip_command`. 8 tests.
- **Settings**: `archive_7zip_path: str = ""`, `archive_wait_for_volumes:
  bool = True`; existing `archive_password_file` and limits apply.
- **API / UI**: none new; link rows show "waiting for volume 3/3" and
  "extracted N files".
- **Hook**: `_run_download()` line 650 before `extract_archive()`: group
  finished links in the same destination with `group_volumes`, extract only
  when `is_set_complete`, starting from `first_volume`; pass
  `password_candidates(link.extra.get("archive_password"), load_passwords(...))`.
- **Duplicates**: **this module duplicates `engine/archives.py`**, which is
  the live extractor (ZIP, TAR, 7z via py7zr, RAR via rarfile) and already has
  its own member-path guard (`_member_path`), password-file reader
  (`_passwords`) and size limits. Only the volume-set helpers and the external
  7-Zip command are new. Move those two into `engine/archives.py` and delete
  the rest.

#### `netconfig` (M11r)
- **Provides**: `load_network_config(dict)` and `load_network_config_file(path)`
  building `ReconnectConfig`, `RetryPolicy`, `TorrentSettings`,
  `PortMapConfig`, `ScanConfig` and `CategoryRules` from one JSON document,
  with per-section fallback to defaults and an `errors` list. 8 tests.
- **Settings**: this module *is* a second settings file (`network.json`). The
  alternative is typed sections on `Settings` (pydantic sub-models), which
  would also give `POST /api/settings` validation for free.
- **API / UI**: `GET /api/network` with `errors` surfaced as a notification.
- **Hook**: `app.start_server()` (line 77) after `load_settings()`:
  `manager.netcfg = load_network_config_file(app_data_dir() / "network.json")`.
- **Duplicates / decision**: duplicates what `Settings` already does for every
  other feature. Record in `PROJECT_SPEC.md` Open decisions whether M11
  configs live in `settings.json` (recommended, one file, one validator) or in
  `network.json`. If `settings.json` wins, drop this module and keep each
  section's `*_from_dict` loader.

## 5. Duplication and overlap summary

| Orphan | Live code it duplicates or competes with | Recommendation |
|---|---|---|
| `archives` | `engine/archives.py` path guard, password reader, limits | Merge volume-set helpers and 7-Zip command into `engine/archives.py`; delete the rest |
| `notifications.NotificationCenter` | `server._notifications` deque and inline notice dict | Replace the deque; align `time`/`timestamp` keys with the UI |
| `recovery.RetryPolicy` | `downloader._stream()` retry loop, `manager._fail()` backoff | One policy object used by both |
| `segments.SegmentPlanner` | fixed range split in `downloader.run()` | Replace the static split |
| `queues.QueueManager` | `manager._ready` + `max_parallel` | Main queue's limit is `max_parallel` |
| `scheduler`, `speedprofiles`, `settings.speed_limit_kbps` | three sources of the speed limit | Add one `effective_limit()` resolver |
| `categories.folder_for` | `engine/packagizer.plan_packagizer` subdirs | Categories are the Packagizer's default |
| `cookies` | `http_client._cookies_for`, `extrules.scope_cookies` | One cookie-matching helper |
| `accountauth.pick_account` | `AccountManager.for_domain` | Replace in `PluginContext.account_for` |
| `siteauth.CredentialStore` | `AccountManager` (DPAPI, `accounts.json`) | One persistence layer |
| `linkcheck._filename_from_cd` | `downloader._cd_filename`, `direct.py` HEAD sniff | Share one Content-Disposition parser |
| `hostlimits.host_of` | `siteauth.host_of`, `extrules.host_of` | One helper |
| `spider.extract_links` | `plugins/dirindex.py`, `generic.py` | Acceptable; different inputs |
| `netconfig` | `settings.Settings` | Decide one config file (section 4.5) |

## 6. Adjacent findings (not orphans, found while tracing hooks)

- `server.py:132-133` reads `allow_remote_http` and `trusted_proxies` from
  settings with `getattr(..., default)`, but `Settings` declares neither, so
  the M12b remote policy is stuck at its defaults and cannot be edited from
  the UI or `POST /api/settings`.
- `settings.verify_hash` is declared, shown nowhere, and used nowhere
  (REVIEW B14); `hashverify` is the module that should consume it.
- `BrowserCaptchaSolver` (M8p) is reachable but always returns "no clearance
  store": `clearance.ClearanceStore` is never constructed and nothing puts a
  `clearance_store` into `CaptchaRequest.extra`. The M6c/M6i/M8n browser path
  is therefore as dead at runtime as the orphans.
- `notifications.captcha_waiting_notification` (M12k) has no runtime caller.
  The audit first ran on Hermes's unpushed M12k commit, which also lacked a
  test; master now carries the pre-verified patch (`08359ff`), which tests it.
  The M12k change still touched only orphan modules.
- `SET_FIELDS` in `app.js` covers 21 of the 50 settings keys. Not editable in
  the UI: the NopeCHA key, Tesseract, audio and browser-solver toggles and
  paths, `media_preset`, `media_ffmpeg_path`, `timeout_s`, `user_agent`,
  `extra_headers`, `ua_pool`, `captcha_manual_timeout_s`,
  `captcha_auto_failover`, `stop_at_paywall`, `grab_timeout_s`, `theme`,
  `poll_ms`, `keep_partials`, `chunk_size_kb`. Same class of gap as this
  audit.
- `requirements.txt` lacks `pycryptodomex` (needed by `cnl`); `pyproject.toml`
  has no dependency list at all.

## 7. Recommended wiring order

Each milestone is small (one to four modules), adds its settings keys, API
routes and UI in the same change, and ends with something a user can see in
the running app. Order favours spec headline features (captcha, integrity,
notifications) and zero-dependency modules first. Every milestone keeps
`pytest` at 804 passed, 1 xfailed plus its own new tests, and `--smoke-test`
passing with no new required package.

### W1. Notification center and toasts (`notifications`)
Replace the server deque with `NotificationCenter`, add toasts through the
tray icon, persist history, add read state.
**Accept**: finish a download in the GUI build and a Windows toast appears;
the Notifications tab shows an unread badge that clears on "mark all read";
restart the app and the history is still there; `GET /api/notifications`
returns `id`, `kind`, `title`, `message`, `time`, `read`.

### W2. Captcha inbox, challenge detection and harvest page (`captcha.inbox`, `challenge`, `captcha.harvest`)
Detect challenges in the HTTP client, park them in the inbox, show them in a
Captchas panel, push a "captcha waiting" notification and SSE event.
**Accept**: a fixture page that returns a Cloudflare interstitial (status 503,
`server: cloudflare`, "Just a moment") parks the link as `captcha` with kind
`cf_js` instead of `error`; the Captchas panel lists it with host and kind;
the Notifications tab shows "Captcha waiting on <host>"; solving or skipping
from the panel resumes or parks the link; two links on the same host share
one inbox entry.

### W3. Hash verification and resume validation (`hashverify`, `recovery`)
Verify finished files when a hash is known, validate `.part` files with
ETag/Last-Modified before resuming, unify retry backoff.
**Accept**: queue the smoke-test wheel with its known SHA-256 in the "expected
hash" field and the finished row shows "hash ok"; with a wrong hash it shows
`error: hash mismatch`; change the file on the local test server between
pause and resume and the link restarts from zero instead of finishing with a
corrupt file; `settings.verify_hash` toggles the behaviour.

### W4. Categories, per-host limits and scheduler (`categories`, `hostlimits`, `scheduler`)
Pure logic with small Settings tables.
**Accept**: an `.mp4` lands in `Downloads/Video` and a `.zip` in
`Downloads/Archives` unless a Packagizer rule matches; with `max_per_host=1`
two links from the same host download one after the other while a third host
runs in parallel; a scheduler window "pause 09:00-17:00 Mon-Fri" keeps new
links `waiting` with the note "paused by scheduler" and a "limit 500 kB/s"
window caps the live speed graph.

### W5. Speed profiles, data caps and named queues (`speedprofiles`, `queues`)
Tray submenu and header dropdown for profiles; queue tabs.
**Accept**: switching the tray menu to "Gaming" drops an active download to
at most 512 kB/s within five seconds; a 10 MB daily cap stops the queue with
"data cap reached" after 10 MB and resumes the next day; creating a "Night"
queue with `max_active=1`, moving two links into it and pausing it keeps them
waiting until it is resumed, while Main keeps downloading.

### W6. Dynamic segmentation (`segments`)
Replace the fixed range split in the downloader.
**Accept**: a 200 MiB file from the local range server with 8 connections
finishes with the correct SHA-256 and the sidecar shows more than 8 segments
after one connection was throttled; a kill-and-resume mid-transfer still
finishes correctly; the benchmark test records throughput against a
single-connection download as spec 6a requires.

### W7. Proxies and server/proxy authentication (`proxies`, `siteauth`)
Proxy list with rules and failover; saved Basic and Digest logins.
**Accept**: with a rule routing `*.example.com` to `direct` and an
unreachable first proxy in the list, downloads from the rule host succeed and
other hosts fail over to the second proxy, shown in the row; a Basic-auth
protected local URL downloads after saving credentials for its host and the
password is stored protected in `settings.json`.

### W8. LinkGrabber v2: online check, spider, Click'n'Load (`linkcheck`, `spider`, `cnl`, `cnl_server`)
Add `pycryptodomex` to requirements first.
**Accept**: pasting two URLs, one of them 404, shows "offline" on one row and
size and filename on the other; "Spider a site" on a local two-level test
site lists its file links and skips paths disallowed by `robots.txt`;
`http://127.0.0.1:9666/jdcheck.js` returns `jdownloader=true` and a
Click'n'Load button on a test page adds its links to the LinkGrabber review
list without starting anything.

### W9. Accounts: usage, cookies, debrid (`usage`, `cookies`, `accountauth`, `debrid`, `premium`)
Accounts tab learns cookies, quotas and debrid tokens.
**Accept**: pasting a `cookies.txt` into an account sends those cookies on
the next grab for that host (visible in the local test server's log); a
Real-Debrid account with an API token resolves a supported hoster link to a
direct download and the row shows "via realdebrid"; the Accounts tab shows
today's usage and refuses to start a second download when `max_parallel=1`
for that account.

### W10. Antivirus scan and archive volume sets (`avscan`, `archives`)
Fold the volume helpers into `engine/archives.py`.
**Accept**: with scanning enabled and Defender present, a finished download
shows "clean" within the timeout and the queue keeps running meanwhile; with
the custom command `cmd /c exit 2` the row shows "infected" and the file is
moved to `quarantine/`; downloading `a.part1.rar`, `a.part2.rar`,
`a.part3.rar` extracts once, after the last volume finishes.

### W11. BitTorrent (`torrent`, `trackers`, `portmap`)
Optional `libtorrent` extra; the default install and `--smoke-test` must
work without it. State the dependency's RAM and disk impact per spec 2.
**Accept**: pasting a magnet link for a Linux ISO shows a torrent row with
peers and progress and the files land in the download dir; unticking a file
in the picker skips it; the Trackers settings table shows announce health;
port mapping reports "mapped via UPnP" or the manual port and the
reachability test gives a yes/no.

### W12. Plugin tooling (`plugins.sdk`, `plugins.updates`, `plugins.permissions`, `plugins.sandbox`)
**Accept**: `opengrab --validate-template bad.json` prints the error and
exits 1; "Check for template updates" against a local index installs a new
template that appears after rescan; a user plugin that fetches a host outside
its granted list fails with a permission error shown in Plugin health, and
granting the host in the Permissions dialog makes it work.

## 8. Defer or drop

- **`plugins.marketplace`** (M7r): defer. Needs a hosted, signed catalog and a
  signature implementation that do not exist. Keep the tests.
- **`reconnect`** (M11d): defer behind W7. Needs a user-supplied router
  command to do anything; the README already lists it as not implemented.
- **`netconfig`** (M11r): drop in favour of typed sections on `Settings`, or
  keep only if the Open decision chooses a second config file. Either way the
  per-module `*_from_dict` loaders stay.
- **`archives`** (M11q): drop everything except the volume-set helpers and
  `seven_zip_command`, which move into `engine/archives.py` (W10).
- **`plugins.sandbox`** (M7q): keep, but rename the spec claim; it is a
  network and cookie guard, not the QuickJS or subprocess sandbox in spec 6c.
- **`captcha.harvest`** (M6e): keep, but expect it to be used mostly inside
  the Edge CDP solver window; widget tokens from a `127.0.0.1` origin are
  rejected by most sites.
- **`siteauth`** NTLM, Negotiate and Kerberos: keep Basic and Digest in W7
  and verify SSPI support in the shipped libcurl before promising the rest.

## Appendix A. Script output (2026-10-30)

```
package: src/opengrab  modules: 92  reachable: 57  unreachable: 35
roots: app, server, cli, smoke, __main__ (+ plugins/builtin/* loaded dynamically)

Modules not reachable from any runtime entry point:
  accountauth
  archives
  avscan
  captcha.harvest
  captcha.inbox
  categories
  challenge
  cnl
  cnl_server
  cookies
  debrid
  hashverify
  hostlimits
  linkcheck
  netconfig
  notifications
  plugins.marketplace
  plugins.permissions
  plugins.sandbox
  plugins.sdk
  plugins.updates
  portmap
  premium
  proxies
  queues
  reconnect
  recovery
  scheduler
  segments
  siteauth
  speedprofiles
  spider
  torrent
  trackers
  usage
```

`--verbose` additionally shows, for each orphan, which package modules it
imports and which modules import it; `--json` emits the same data for tools.
