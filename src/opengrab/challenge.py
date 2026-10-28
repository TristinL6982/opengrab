"""Challenge detection for anti-bot protection (pure, standard library only).

Detects Cloudflare (JS interstitial, managed Turnstile, 1020 block),
reCAPTCHA, hCaptcha, standalone Turnstile and DDoS-Guard pages.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union

BODY_LIMIT = 200000


class ChallengeKind(str, Enum):
    NONE = "none"
    CF_JS = "cf_js"
    CF_TURNSTILE = "cf_turnstile"
    CF_BLOCK = "cf_block"
    RECAPTCHA = "recaptcha"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"
    DDOS_GUARD = "ddos_guard"


@dataclass
class Challenge:
    kind: ChallengeKind
    sitekey: Optional[str] = None
    ray_id: Optional[str] = None
    reason: str = ""


_SITEKEY_RE = re.compile(r"""data-sitekey\s*=\s*["']([^"']+)["']""", re.I)
_RAY_RE = re.compile(r"Ray ID:\s*(?:<[^>]*>\s*)*([0-9a-fA-F]{8,}(?:-[A-Za-z]{3})?)")


def _turnstile_markers(body: str) -> bool:
    return "challenges.cloudflare.com/turnstile" in body or 'class="cf-turnstile"' in body


def detect_challenge(status: int, headers: dict, body: Union[str, bytes, None]) -> Challenge:
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    text = (body or "")[:BODY_LIMIT]

    server = h.get("server", "").strip().lower()
    m = _SITEKEY_RE.search(text)
    sitekey = m.group(1) if m else None
    ray_id = h.get("cf-ray")
    if not ray_id:
        rm = _RAY_RE.search(text)
        ray_id = rm.group(1) if rm else None

    def make(kind, reason):
        return Challenge(kind, sitekey=sitekey, ray_id=ray_id, reason=reason)

    # Rule 1: Cloudflare interstitial / managed challenge
    cf_mitigated = h.get("cf-mitigated", "").strip().lower() == "challenge"
    cf_interstitial = (status in (403, 503) and server == "cloudflare" and
                       ("Just a moment" in text or "/cdn-cgi/challenge-platform/" in text))
    if cf_mitigated or cf_interstitial:
        if _turnstile_markers(text):
            return make(ChallengeKind.CF_TURNSTILE, "cloudflare turnstile challenge")
        return make(ChallengeKind.CF_JS, "cloudflare js challenge")

    # Rule 2: Cloudflare block page (a cf-ray header also identifies Cloudflare)
    if status == 403 and (server == "cloudflare" or "cf-ray" in h) and (
            "error code: 1020" in text or "Access denied" in text or "cf-error-details" in text):
        return make(ChallengeKind.CF_BLOCK, "cloudflare access denied")

    # Rule 3: DDoS-Guard
    if (server == "ddos-guard" and status == 403) or ("ddos-guard.net/" in text and "check" in text):
        return make(ChallengeKind.DDOS_GUARD, "ddos-guard challenge")

    # Rule 4: reCAPTCHA
    if 'class="g-recaptcha"' in text or "www.google.com/recaptcha/api.js" in text or "recaptcha/api2" in text:
        return make(ChallengeKind.RECAPTCHA, "reCAPTCHA challenge")

    # Rule 5: hCaptcha
    if 'class="h-captcha"' in text or "hcaptcha.com/1/api.js" in text:
        return make(ChallengeKind.HCAPTCHA, "hCaptcha challenge")

    # Rule 6: standalone Turnstile
    if _turnstile_markers(text):
        return make(ChallengeKind.TURNSTILE, "turnstile challenge")

    return Challenge(ChallengeKind.NONE, reason="")