"""Challenge detection for anti-bot protection.

Provides detection for various bot detection mechanisms including:
- Cloudflare challenges (JS, Turnstile, block)
- reCAPTCHA
- hCaptcha
- Turnstile
- DDoS-Guard
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional
import re


class ChallengeKind(str, Enum):
    """Types of bot detection challenges."""
    NONE = "none"
    CF_JS = "cf_js"  # Cloudflare "Just a moment..." interstitial
    CF_TURNSTILE = "cf_turnstile"  # Cloudflare managed challenge with turnstile widget
    CF_BLOCK = "cf_block"  # Cloudflare 1020/access denied/ray-id error page
    RECAPTCHA = "recaptcha"  # Google reCAPTCHA
    HCAPTCHA = "hcaptcha"  # hCaptcha
    TURNSTILE = "turnstile"  # Standalone turnstile widget on normal page
    DDOS_GUARD = "ddos_guard"  # DDoS-Guard challenge


@dataclass
class Challenge:
    """Represents a detected bot protection challenge."""
    kind: ChallengeKind
    sitekey: Optional[str] = None
    ray_id: Optional[str] = None
    reason: str = ""


def detect_challenge(status: int, headers: dict, body: str | bytes) -> Challenge:
    """
    Detect the type of bot challenge from an HTTP response.
    
    Args:
        status: HTTP status code
        headers: HTTP headers (case-insensitive)
        body: Response body (string or bytes)
        
    Returns:
        Challenge object with kind, sitekey, ray_id, and reason
        
    Rules (first match wins):
    1. Header `cf-mitigated: challenge`, or status 403/503 with `server: cloudflare` 
       and body containing "Just a moment" or "/cdn-cgi/challenge-platform/": 
       CF_TURNSTILE if the body also contains "challenges.cloudflare.com/turnstile" 
       or 'class="cf-turnstile"', else CF_JS.
    2. Status 403 with `server: cloudflare` and body containing "error code: 1020" or 
       "Access denied" or "cf-error-details": CF_BLOCK.
    3. `server: ddos-guard` with status 403, or body containing "ddos-guard.net/" and "check": DDOS_GUARD.
    4. Body containing 'class="g-recaptcha"' or "www.google.com/recaptcha/api.js" or "recaptcha/api2":
       RECAPTCHA.
    5. Body containing 'class="h-captcha"' or "hcaptcha.com/1/api.js": HCAPTCHA.
    6. Body containing 'class="cf-turnstile"' or "challenges.cloudflare.com/turnstile": TURNSTILE.
    7. Otherwise NONE.
    """
    # Normalize headers to lowercase for case-insensitive matching
    normalized_headers = {k.lower(): v for k, v in (headers or {}).items()}
    
    # Handle body as string
    if isinstance(body, bytes):
        body_str = body.decode("utf-8", errors="replace")[:200000]
    else:
        body_str = body[:200000]
    
    # Extract ray_id from headers or body for CF_BLOCK case
    ray_id = None
    if "cf-ray" in normalized_headers:
        ray_id = normalized_headers["cf-ray"]
    
    # Rule 1: Cloudflare mitigation challenge (specific case)
    if normalized_headers.get("cf-mitigated") == "challenge":
        # Check for turnstile
        if ("challenges.cloudflare.com/turnstile" in body_str or 
                'class="cf-turnstile"' in body_str):
            return Challenge(ChallengeKind.CF_TURNSTILE, reason="cloudflare turnstile challenge")
        else:
            return Challenge(ChallengeKind.CF_JS, reason="cloudflare js challenge")
    
    # Special case: Cloudflare with specific "Just a moment" and/or "/cdn-cgi/challenge-platform/"
    if (status in (403, 503) and normalized_headers.get("server", "").lower() == "cloudflare" and 
            ("Just a moment" in body_str or "/cdn-cgi/challenge-platform/" in body_str)):
        # Check for turnstile
        if ("challenges.cloudflare.com/turnstile" in body_str or 
                'class="cf-turnstile"' in body_str):
            return Challenge(ChallengeKind.CF_TURNSTILE, reason="cloudflare turnstile challenge")
        else:
            return Challenge(ChallengeKind.CF_JS, reason="cloudflare js challenge")
    
    # Rule 2: Cloudflare block with error code
    if (status == 403 and normalized_headers.get("server", "").lower() == "cloudflare" and 
            ("error code: 1020" in body_str or "Access denied" in body_str or 
             "cf-error-details" in body_str)):
        return Challenge(ChallengeKind.CF_BLOCK, ray_id=ray_id, reason="cloudflare access denied")
    
    # Rule 3: DDoS-Guard challenge
    if (normalized_headers.get("server", "").lower() == "ddos-guard" and status == 403) or \
       ("ddos-guard.net/" in body_str and "check" in body_str):
        return Challenge(ChallengeKind.DDOS_GUARD, reason="ddos-guard challenge")
    
    # Rule 4: reCAPTCHA detection
    if ('class="g-recaptcha"' in body_str or 
            "www.google.com/recaptcha/api.js" in body_str or 
            "recaptcha/api2" in body_str):
        sitekey = _extract_sitekey(body_str)
        return Challenge(ChallengeKind.RECAPTCHA, sitekey=sitekey, reason="reCAPTCHA challenge")
    
    # Rule 5: hCaptcha detection
    if ('class="h-captcha"' in body_str or 
            "hcaptcha.com/1/api.js" in body_str):
        sitekey = _extract_sitekey(body_str)
        return Challenge(ChallengeKind.HCAPTCHA, sitekey=sitekey, reason="hCaptcha challenge")
    
    # Rule 6: Turnstile detection (standalone)
    if ('class="cf-turnstile"' in body_str or 
            "challenges.cloudflare.com/turnstile" in body_str):
        sitekey = _extract_sitekey(body_str)
        return Challenge(ChallengeKind.TURNSTILE, sitekey=sitekey, reason="turnstile challenge")
    
    # Rule 1: Cloudflare mitigation challenge (specific case)
    if normalized_headers.get("cf-mitigated") == "challenge":
        # Check for turnstile
        if ("challenges.cloudflare.com/turnstile" in body_str or 
                'class="cf-turnstile"' in body_str):
            return Challenge(ChallengeKind.CF_TURNSTILE, reason="cloudflare turnstile challenge")
        else:
            return Challenge(ChallengeKind.CF_JS, reason="cloudflare js challenge")
    
    # Special case: Cloudflare with specific "Just a moment" and/or "/cdn-cgi/challenge-platform/"
    if (status in (403, 503) and normalized_headers.get("server", "").lower() == "cloudflare" and 
            ("Just a moment" in body_str or "/cdn-cgi/challenge-platform/" in body_str)):
        # Check for turnstile
        if ("challenges.cloudflare.com/turnstile" in body_str or 
                'class="cf-turnstile"' in body_str):
            return Challenge(ChallengeKind.CF_TURNSTILE, reason="cloudflare turnstile challenge")
        else:
            return Challenge(ChallengeKind.CF_JS, reason="cloudflare js challenge")
    
    # Rule 2: Cloudflare block with error code
    if (status == 403 and normalized_headers.get("server", "").lower() == "cloudflare" and 
            ("error code: 1020" in body_str or "Access denied" in body_str or 
             "cf-error-details" in body_str)):
        return Challenge(ChallengeKind.CF_BLOCK, ray_id=ray_id, reason="cloudflare access denied")
    
    # Rule 7: No challenge
    return Challenge(ChallengeKind.NONE)


def _extract_sitekey(body_str: str) -> Optional[str]:
    """Extract sitekey from HTML body."""
    # Look for data-sitekey attribute in various forms
    match = re.search(r'data-sitekey=["\']([^"\']*)["\']', body_str)
    if match:
        return match.group(1)
    
    # If no sitekey found, return None
    return None