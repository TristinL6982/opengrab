"""
LinkGrabber helpers: smart paste URL extraction and duplicate detection.
"""

import re
from urllib.parse import urlsplit, urlunsplit


URL_RE = re.compile(r"(?:https?|ftp)://[^\s<>\"'\)\]]+|magnet:\?[^\s<>\"']+", re.IGNORECASE)


def extract_urls(text: str) -> list:
    # Replace hxxp and hxxps with http and https
    text = text.replace("hxxp://", "http://")
    text = text.replace("hxxps://", "https://")

    matches = URL_RE.findall(text)
    urls = []
    seen = set()

    for match in matches:
        # Strip trailing punctuation from the matched string for deduplication
        url_part = match.rstrip(".,;:!?")
        
        if not url_part:
            continue
            
        normalized = normalize_url(url_part)

        if normalized not in seen:
            seen.add(normalized)
            urls.append(url_part)  # Return actual URL without trailing punctuation

    return urls


def normalize_url(url: str) -> str:
    # Magnet links
    if url.lower().startswith("magnet:"):
        clean_url = url.strip()
        # If we have a malformed "magnet:/?xt=..." with incorrect slash, correct it
        # But only check if the input originally had a slash in that position as seen from regex match  
        if len(clean_url) >= 8 and clean_url[:8] == "magnet:/?":
            return "magnet:?" + clean_url[8:]
        return clean_url

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Normalize port
    if scheme == "http" and parsed.port == 80:
        netloc = netloc.rsplit(":", 1)[0]
    elif scheme == "https" and parsed.port == 443:
        netloc = netloc.rsplit(":", 1)[0]
    elif scheme == "ftp" and parsed.port == 21:
        netloc = netloc.rsplit(":", 1)[0]

    # Normalize path
    path = parsed.path
    if not path:
        path = "/"

    # Drop fragment but keep query
    normalized = urlunsplit((scheme, netloc, path, parsed.query, ""))

    return normalized


def dedupe(urls, existing=()) -> list:
    seen = set()
    result = []
    existing_normalized = set(normalize_url(url) for url in existing)

    for url in urls:
        normalized = normalize_url(url)
        if normalized not in existing_normalized and normalized not in seen:
            seen.add(normalized)
            result.append(url)

    return result
