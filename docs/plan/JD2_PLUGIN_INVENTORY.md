# JDownloader 2 plugin inventory (for OpenGrab)

Generated 2026-10-27 by Grok Bot. Raw data: `/workspace/jd2/jd2_plugins.csv` (one row per plugin class). Scripts: `/workspace/jd2/tools/`.

## Source used
- **Official SVN (`svn://svn.appwork.org/jdownloader/trunk`) was not used.** The box has no `svn` client, and `apt-get install subversion` failed because the Debian mirrors returned 500 errors or unsigned InRelease files.
- **Used instead:** the GitHub mirror `mycodedoesnotcompile2/jdownloader_mirror`, which a GitHub Action updates daily with `svn checkout svn://svn.jdownloader.org/jdownloader/trunk`. Commit `9c617274f4803430b3c7f1af46a21ef95ff1c9e9` ("mirroring bot - 2026/10/27", 2026-10-27 06:30 ET). Sparse checkout of `svn_trunk/src/jd/plugins` and `svn_trunk/src/org/jdownloader/plugins/components` into `/workspace/jd2/src_mirror`.
- The mirror does not record the SVN revision. The highest `$Revision$` keyword in any plugin file is **r53761**, so the snapshot is at least at that revision.
- `mirror/jdownloader` on GitHub was last pushed in September 2024, so it is stale and was not used.

## Website claim vs. the actual code
jdownloader.org/home/features says, word for word: **"About 110 hoster and over 300 decrypt plug-ins"**. The same list also says "Runs on Java 1.5 or higher", "Reconnect with JDLiveHeaderScripts: (1400 router supported)", "Webupdate", and "Integrated package manager for additional modules (eg. Webinterface, Shutdown)". These are JDownloader 0.x/1 era items, so the page is very outdated.

| Metric | Hoster | Decrypter (crawler) | Total |
|---|---|---|---|
| Plugin classes (`@HostPlugin` / `@DecrypterPlugin`) | 1,419 | 858 | 2,277 |
| Site entries (one `String[]` per site in `getPluginDomains()`, which JD turns into one plugin per host) | 1,925 (1,702 not counting the `Offline` plugin) | 1,165 | 3,090 |
| Distinct domains, including aliases | 5,194 | 2,609 | 6,455 |
| Distinct domains, not counting the `Offline` plugin | 3,073 | 2,609 | 4,409 |

- The `Offline` hoster is a catch-all for dead sites: 223 site entries and 2,455 domains, which it marks offline right away. Don't count those as real coverage.
- Real coverage is about **1,700 live hoster sites plus 1,165 crawler sites**, roughly 15 times the website's claim.
- About 28 classes have no parseable domains. They are mostly abstract bases, plus a few that build their lists at runtime (for example PeerTube, KinoxTo, and the safelinking crawler `SflnkgNt`).
- Hosters include 69 multihost/debrid plugins and 10 paid captcha-solver plugins (2captcha, AntiCaptcha, CapMonster, DeathByCaptcha, 9kw, and others).

## Template and generic plugins (the big coverage multipliers)
| Base class | Classes | Site entries | Domains |
|---|---|---|---|
| XFileSharingProBasic (XFileSharing Pro script) | 315 | 317 | 839 |
| KernelVideoSharingComV2 (KVS tube script) | 95 | 200 | 385 |
| MightyScriptAdLinkFly (link shorteners) | 21 | 21 | 215 |
| PornEmbedParser | 38 | 43 | 184 |
| YetiShareCore (YetiShare script) | 85 | 86 | 111 |
| TurbobitCore | 2 | 2 | 51 |
| UseNet | 27 | 26 | 26 |
| EvilangelCore | 17 | 17 | 17 |

These single classes cover many sites each:
- `KernelVideoSharingComV2HostsDefault`: 98 sites
- `GenericRedirectors`: 90 sites
- `UnknownPornScript2`: 37 sites
- `PornportalCom`: 29 sites
- `GenericYetiShareFolder`: 22 sites
- `VoeSxCrawler`: 139 mirror domains

Fully generic plugins: `DirectHTTP` (any http link), `GenericM3u8`/`GenericM3u8Decrypter`, `GenericHTTPDirectoryIndexCrawler`, `GenericXFileShareProFolder`, `GenericNZBDecrypter`, and `LinkCrawlerDeepInspector`. In total, 665 classes are flagged `generic_template=y`.

## Popular hosts and whether JD2 covers them
"Account/premium" means the plugin calls `enablePremium()` or uses accounts. Captcha types listed here include types inherited from the plugin's template base class.

| Host | Domain checked | JD2 covers? | JD2 plugin(s) (h=hoster, d=decrypter) | Account/premium | Captcha/anti-bot code paths |
|---|---|---|---|---|---|
| Mega | mega.nz | yes | h:MegaConz, d:MegaConz | y | - |
| Rapidgator | rapidgator.net | yes | h:RapidGatorNet, d:RapidGatorNetFolder | y | cloudflare_turnstile;image_captcha;recaptcha_v2 |
| 1fichier | 1fichier.com | yes | h:OneFichierCom, d:OneFichierComFolder | y | - |
| Uploaded | uploaded.net | offline-only | Offline | n | - |
| Nitroflare | nitroflare.com | yes | h:NitroFlareCom, d:NitroFlareComFolder | y | hcaptcha;image_captcha;recaptcha_v2 |
| Turbobit | turbobit.net | yes | h:TurboBitNet | n | cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2 |
| Mediafire | mediafire.com | yes | h:MediafireCom, d:MediafireComFolder | y | recaptcha_v2 |
| Google Drive | drive.google.com | yes | h:GoogleDrive, d:GoogleDriveCrawler | y | recaptcha_v2 |
| Dropbox | dropbox.com | yes | h:DropboxCom, d:DropBoxComCrawler | y | - |
| OneDrive | onedrive.live.com | yes | h:OneDriveLiveCom, d:OneDriveLiveComCrawler | n | - |
| pixeldrain | pixeldrain.com | yes | h:PixeldrainCom, d:PixeldrainComFolder | y | recaptcha_v2 |
| Gofile | gofile.io | yes | h:GofileIo, d:GoFileIoCrawler | y | - |
| YouTube | youtube.com | yes | h:YoutubeDashV2, d:TbCmV2 | y | - |
| Vimeo | vimeo.com | yes | h:VimeoCom, d:VimeoComDecrypter, d:VimeoComDecrypterPlay | y | - |
| Twitter/X | x.com | yes | h:TwitterCom, d:TwitterComCrawler | y | - |
| Instagram | instagram.com | yes | h:InstaGramCom, d:InstaGramComDecrypter | y | - |
| TikTok | tiktok.com | yes | h:TiktokCom, d:TiktokComCrawler | y | - |
| Reddit | reddit.com | yes | h:RedditCom, d:RedditComCrawler | y | - |
| Pornhub | pornhub.com | yes | h:PornHubCom, d:PornHubComGallery, d:PornHubComVideoCrawler | y | recaptcha_v2 |
| XVideos | xvideos.com | yes | h:XvideosCom, d:XvideosComProfile | y | - |
| Filecrypt | filecrypt.cc | yes | d:FileCryptCc | n | click_captcha;cutcaptcha;image_captcha;powcaptcha;recaptcha_v2 |
| Keeplinks | keeplinks.org | yes | d:KeepLinksMe | n | cloudflare_antibot;cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2;solvemedia |
| LinkSnappy | linksnappy.com | yes | h:LinkSnappyCom | y | - |
| Keep2Share | k2s.cc | yes | h:Keep2ShareCc, d:Keep2ShareCcDecrypter | n | - |
| Ddownload | ddownload.com | yes | h:DdownloadCom | y | cloudflare_antibot;cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2 |
| Katfile | katfile.com | yes | h:KatfileCom, d:GenericXFileShareProFolder | y | cloudflare_antibot;cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2 |
| Uptobox | uptobox.com | offline-only | Offline | n | - |
| Hitfile | hitfile.net | yes | h:HitFileNet | n | cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2 |
| Fikper | fikper.com | yes | h:FikperCom | y | hcaptcha;recaptcha_v2 |
| Filefactory | filefactory.com | yes | h:FileFactory, d:FilefactoryComFolder | y | - |
| Real-Debrid | real-debrid.com | yes | h:RealDebridCom | y | recaptcha_v2 |
| AllDebrid | alldebrid.com | yes | h:AllDebridCom, d:AlldebridComFolder | y | - |
| Premiumize | premiumize.me | yes | h:PremiumizeMe, d:PremiumizeMeZeveraFolder | y | - |
| Twitch | twitch.tv | yes | h:TwitchTv, d:TwitchTvDecrypt | y | - |
| Dailymotion | dailymotion.com | yes | h:DailyMotionCom, h:DailyMotionComV2, d:DailyMotionComDecrypter, d:DailyMotionComDecrypterV2 | y | - |
| SoundCloud | soundcloud.com | yes | h:SoundcloudCom, d:SoundCloudComDecrypter | y | - |
| Facebook | facebook.com | yes | h:FaceBookComVideos | y | image_captcha |
| Imgur | imgur.com | yes | h:ImgurComHoster, d:ImgurComGallery | y | - |
| Pinterest | pinterest.com | yes | h:PinterestCom, d:PinterestComDecrypter | y | recaptcha_v2 |
| Flickr | flickr.com | yes | h:FlickrCom, d:FlickrComCrawler | y | - |
| Bunkr | bunkr.si | yes | h:Bunkr, d:BunkrAlbum | n | cloudflare_antibot |
| Cyberdrop | cyberdrop.me | yes | h:CyberdropMe, d:CyberdropMeAlbum | n | - |
| Krakenfiles | krakenfiles.com | yes | h:KrakenfilesCom, d:KrakenfilesComFolder | y | cloudflare_turnstile;recaptcha_v2 |
| Send.cm | send.cm | yes | h:SendCm, h:SendNow, d:SendCmFolder, d:SendNowFolder | y | cloudflare_antibot;cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2 |
| WeTransfer | wetransfer.com | yes | h:WeTransferCom, d:WeTransferComFolder | y | - |
| Box.com | box.com | yes | h:BoxCom, d:BoxCom, d:BoxComCrawler | n | cloudflare_antibot |
| pCloud | pcloud.com | yes | h:PCloudCom, d:PCloudComFolder | y | recaptcha_v2 |
| Yandex Disk | disk.yandex.ru | yes | h:DiskYandexNet | y | image_captcha |
| Doodstream | doodstream.com | yes | h:DoodstreamCom, d:DoodstreamComFolder | y | cloudflare_antibot;cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2 |
| Voe | voe.sx | yes | h:VoeSx, d:VoeSxCrawler | y | cloudflare_antibot;cloudflare_turnstile;hcaptcha;image_captcha;recaptcha_v2 |
| Streamtape | streamtape.com | yes | h:StreamtapeCom | n | - |
| Archive.org | archive.org | yes | h:ArchiveOrg, d:ArchiveOrgCrawler | y | - |
| GitHub | github.com | NO | - | n | - |
| Usenet (NZB) | nzb | yes | d:GenericNZBDecrypter | n | - |
| Uploadgig | uploadgig.com | yes | h:UploadgigCom | y | cloudflare_antibot;recaptcha_v2 |
| DepositFiles | depositfiles.com | yes | h:DepositFiles, d:DepositfilesComFolder | y | cloudflare_antibot;cloudflare_turnstile;recaptcha_v2 |
| Mixdrop | mixdrop.co | yes | h:MixdropCo | n | cloudflare_antibot;cloudflare_turnstile;recaptcha_v2 |
| Rumble | rumble.com | yes | d:RumbleCom | n | - |
| Bandcamp | bandcamp.com | yes | h:BandCampCom, d:BandCampComDecrypter | n | - |
| ouo.io | ouo.io | yes | d:OuoIo | n | cloudflare_antibot;recaptcha_v2 |
| Linkvertise | linkvertise.com | NO | - | n | - |
| Shrinkme | shrinkme.io | yes | d:ShrinkeMe | n | cloudflare_antibot;hcaptcha;recaptcha_v2 |
| safelinking | safelinking.net | NO | - | n | - |
| DirectHTTP | http links | yes | h:DirectHTTP | n | cloudflare_antibot |
| Generic m3u8 | m3u8 | yes | h:GenericM3u8, d:GenericM3u8Decrypter | n | - |

Gaps and dead hosts:
- Uploaded.net and Uptobox exist only in `Offline`, since both sites are dead.
- There is no dedicated plugin for Linkvertise or GitHub. GitHub release files are handled as plain links by DirectHTTP.

## Captcha and anti-bot patterns
These are counts of plugins whose own file uses each captcha type, followed by the total including types inherited from a template base.

| Type | Direct hosters | Direct crawlers | Including inherited |
|---|---|---|---|
| reCAPTCHA v2 | 144 | 57 | 607 |
| hCaptcha | 25 | 11 | 461 |
| Cloudflare Turnstile | 36 | 17 | 453 |
| Image/OCR captcha (`getCaptchaCode`) | 55 | 17 | 388 |
| Cloudflare anti-bot (IUAM, `antiDDoSForHost`/`antiDDoSForDecrypt`, cf_clearance) | 78 | 100 | 602 |

Rarer types: click captcha (10), CutCaptcha (3, used by filecrypt), Altcha (4), SolveMedia (3), PowCaptcha (1).

288 plugins contain their own captcha code. Most of the rest inherit captcha handling from XFileSharingProBasic and YetiShareCore.

How JD2 handles captchas:
- **Detect and solve, not bypass.** Shared helpers (`CaptchaHelperHostPluginRecaptchaV2`, `...HCaptcha`, `...CloudflareTurnstile`, `containsRecaptchaV2Class(br)` checks) find the site key and send the challenge to a central solver queue.
- **Ways to solve:** the user solves it in a browser, or JD's own OCR/"JAC" handles it, or it goes to a paid solver service, or MyJDownloader remote solving.
- **Cloudflare:** JD2 handles the older IUAM JavaScript challenge in `antiDDoSFor*`. Turnstile is a token captcha that a human or solver must complete. JD2 has **no** headless-browser bypass of the modern Cloudflare managed challenge.

## Recommendation for OpenGrab
1. **Copy the architecture, not the code.**
   - Use a plugin registry keyed by domain list plus URL regex, with separate hoster and crawler plugins.
   - Each plugin declares its domains, aliases, and dead domains, and gets an `Offline` fallback.
   - Build template base classes for the big file-host scripts: XFileSharing, YetiShare, KVS, and AdLinkFly shorteners. A new site then takes a few lines of config instead of a new plugin. Those four templates alone cover about 1,550 domains.
   - Keep a debrid/multihost interface and a pluggable captcha-solver interface (browser, local model, and 2captcha/CapSolver-style APIs).
2. **Use yt-dlp for video and social sites.** It supports more than 1,800 extractors, covering YouTube, Vimeo, X, Instagram, TikTok, Reddit, Twitch, Dailymotion, SoundCloud, and the tube sites. Wrap it as one plugin instead of reimplementing hundreds of JD2 per-site plugins.
3. **To beat JD2 on Cloudflare**, add a real-browser challenge solver (Playwright or undetected-chromium) that reuses cf_clearance cookies and gets Turnstile tokens. This is something JD2 lacks.

### First wave of about 25 to implement
1. Generic HTTP/direct links, with resume and multiple connections
2. HLS/m3u8 and DASH
3. yt-dlp bridge (YouTube, Vimeo, X, Instagram, TikTok, Reddit, Twitch, and more)
4. Mega (with decryption)
5. Google Drive (file and folder)
6. Mediafire (file and folder)
7. Dropbox
8. OneDrive
9. pixeldrain
10. Gofile
11. 1fichier
12. Rapidgator (free and premium)
13. Nitroflare
14. Turbobit/Hitfile
15. Keep2Share
16. XFileSharing template (gets ddownload, katfile, send.cm and others)
17. YetiShare template
18. Filecrypt crawler (CNL2/DLC, CutCaptcha)
19. Keeplinks/safelinking-style crawlers
20. AdLinkFly shortener template (ouo.io, shrinkme)
21. DLC/RSDF/CCF container import, plus Click'n'Load
22. Real-Debrid
23. AllDebrid
24. Premiumize
25. Bunkr/Cyberdrop, plus a generic directory-index crawler

## License
- **JD2 plugins:** the files carry GPLv3-or-later headers (1,343 of 1,422 hoster files say "GNU General Public License ... version 3"). The copyright holder is "JD-Team".
- **AppWork Utilities:** the core library (`svn_utils`) has its own "AppWork Utilities" license. It is free for non-commercial use, and commercial use needs a paid license.

What this means for OpenGrab:
- **Copying or porting JD2 plugin code**, even translated to Python, makes those parts derivative works. OpenGrab would then have to be distributed under GPLv3, with source and the original attribution. That's fine if OpenGrab is GPLv3, but it rules out a permissive MIT/Apache license.
- **Avoid AppWork Utilities entirely.** Its non-commercial license is not an open-source license and is not compatible with GPL in a clean way.
- **Reimplementing from public behavior is not a derivative work.** That covers site HTML/APIs, URL patterns, and domain lists, since domain lists are facts. Studying JD2 to learn which domains and endpoints exist is fine; copying its code structure line by line is not.
- **Recommendation:** license OpenGrab GPLv3 if direct JD2 ports are wanted. Otherwise clean-room it and use yt-dlp, which is Unlicense/public domain.

## Method caveats
- **How the counts were made.** Domains come from static parsing of `getPluginDomains()` string arrays, referenced classes and fields, `domains` fields, and annotation `names`. Domains built at runtime are missed.
- **`premium_account`:**
  - `y` means the plugin calls `enablePremium()` itself (for crawlers, it uses accounts).
  - `unknown` means only its base class does.
  - Some plugins marked `n` (for example Turbobit and Keep2Share) get account support through their core class in ways the heuristic doesn't catch.
- **Captcha types** come from matching patterns in the source. A type marked "(inherited)" means the template base can handle that type, not that every site using the template shows it.
