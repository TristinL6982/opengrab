"""
LinkGrabber online check: classify links as online, offline or unknown using an injected HEAD function.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import unquote


@dataclass
class LinkStatus:
    url: str
    state: str
    size: Optional[int] = None
    filename: str = ""
    status_code: Optional[int] = None


HeadFn = Callable[[str], tuple]


def _filename_from_cd(value: str) -> str:
    """Extract the filename from a Content-Disposition header value."""
    match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", value, re.IGNORECASE)
    if match:
        return unquote(match.group(1).strip())
    match = re.search(r'filename\s*=\s*"([^"]*)"', value, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"filename\s*=\s*([^;]*)", value, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("'\"").strip()
    return ""


def check_link(url: str, head: HeadFn) -> LinkStatus:
    """Check a single link."""
    try:
        status_code, headers = head(url)
    except Exception:
        return LinkStatus(url=url, state="unknown")

    # Look up headers case-insensitively
    headers_lower = {k.lower(): v for k, v in headers.items()}
    
    # Determine state
    if 200 <= status_code < 400:
        state = "online"
    elif status_code in (404, 410):
        state = "offline"
    else:
        state = "unknown"
        
    # Extract size from Content-Length
    size = None
    cl = headers_lower.get("content-length")
    if cl is not None and cl.isdigit():
        size = int(cl)
        
    # Extract filename from Content-Disposition
    cd = headers_lower.get("content-disposition", "")
    filename = _filename_from_cd(cd)
    
    return LinkStatus(
        url=url,
        state=state,
        size=size,
        filename=filename,
        status_code=status_code
    )


def check_links(urls, head: HeadFn) -> list:
    """Check multiple links, preserving order."""
    return [check_link(u, head) for u in urls]


def summarize(statuses) -> dict:
    """Summarize statuses by state."""
    counts = {"online": 0, "offline": 0, "unknown": 0}
    for s in statuses:
        counts[s.state] += 1
    return counts