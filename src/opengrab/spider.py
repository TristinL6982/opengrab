"""
Website spider: crawl from a start URL with a depth limit and collect file links for the LinkGrabber.
"""

import posixpath
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
import re


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in ('a', 'area'):
            for attr, value in attrs:
                if attr == 'href' and value:
                    self.links.append(value)
        elif tag in ('img', 'source', 'video', 'audio', 'iframe'):
            for attr, value in attrs:
                if attr == 'src' and value:
                    self.links.append(value)


_KEEPS_FRAGMENT = re.compile(r"^https?://(?:[a-z0-9-]+\.)*(?:mega\.nz|mega\.co\.nz|mega\.io|mega\.app|transfer\.it|gofile\.io)/", re.I)


def extract_links(html: str, base_url: str) -> list:
    parser = _LinkParser()
    parser.feed(html)
    seen = set()
    result = []
    for link in parser.links:
        full_url = urljoin(base_url, link)
        # Remove the fragment, except where it carries part of the link: Mega
        # and transfer.it keep the decryption key there, GoFile the file id.
        defragged, fragment = urldefrag(full_url)
        if not (fragment and _KEEPS_FRAGMENT.search(defragged)):
            full_url = defragged
        # Keep only http and https URLs
        if not full_url.startswith(('http://', 'https://')):
            continue
        if full_url not in seen:
            seen.add(full_url)
            result.append(full_url)
    return result


@dataclass
class SpiderOptions:
    max_depth: int = 1
    same_domain: bool = True
    extensions: tuple = ()
    include: tuple = ()
    exclude: tuple = ()
    max_pages: int = 100
    respect_robots: bool = True
    user_agent: str = "OpenGrab"
    delay: float = 0.0


FetchFn = Callable[[str], tuple]


def _extension(url: str) -> str:
    return posixpath.splitext(urlparse(url).path)[1].lower()


def _robots_for(url: str, fetch, cache: dict):
    # Extract scheme and netloc to form the key
    parsed_url = urlparse(url)
    key = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    if key not in cache:
        robots_url = key + "/robots.txt"
        try:
            content_type, body = fetch(robots_url)
            rp = RobotFileParser()
            rp.parse(body.splitlines())
            cache[key] = rp
        except Exception:
            # If fetching robots.txt fails, cache None (meaning allow everything)
            cache[key] = None
    
    return cache[key]


def crawl(start_url: str, fetch: FetchFn, options: SpiderOptions = None, sleep=time.sleep) -> dict:
    if options is None:
        options = SpiderOptions()
    
    extensions = tuple(ext.lower() for ext in options.extensions)
    
    pages = []
    files = []
    errors = []
    visited = set()
    queue = [(start_url, 0)]
    robots_cache = {}
    fetched = 0
    blocked = []
    
    while queue:
        url, depth = queue.pop(0)
        
        # Skip if already visited (this prevents visiting URLs twice during crawling)
        if url in visited:
            continue
        
        # Stop when max_pages is reached
        if len(pages) >= options.max_pages:
            break
            
        # Check robots.txt before fetching if respect_robots is True
        if options.respect_robots:
            rp = _robots_for(url, fetch, robots_cache)
            if rp is not None and not rp.can_fetch(options.user_agent, url):
                blocked.append(url)
                continue
        
        # Apply delay if set and we've fetched at least one page
        if options.delay > 0 and fetched > 0:
            sleep(options.delay)
        
        fetched += 1
        try:
            content_type, body = fetch(url)
        except Exception:
            errors.append(url)
            continue
        
        # Mark as visited after successful fetch
        visited.add(url)
        
        # Only add to pages if fetch was successful 
        pages.append(url)
        
        # If not HTML, don't parse links
        if not content_type.lower().startswith("text/html"):
            continue
        
        for link in extract_links(body, url):
            # Check if it's a file (extension in options.extensions)
            if _extension(link) in extensions:
                # Skip based on exclude regex
                if any(re.search(pattern, link) for pattern in options.exclude):
                    continue
                
                if options.include and not any(
                    re.search(pattern, link) for pattern in options.include
                ):
                    continue
                
                if link not in files:
                    files.append(link)
            else:
                # Page candidate
                if options.same_domain:
                    start_netloc = urlparse(start_url).netloc.lower()
                    link_netloc = urlparse(link).netloc.lower()
                    if link_netloc != start_netloc:
                        continue
                
                if any(re.search(pattern, link) for pattern in options.exclude):
                    continue
                
                # Only add to queue if we haven't exceeded max_depth
                if depth + 1 <= options.max_depth and link not in visited:
                    queue.append((link, depth + 1))
    
    return {"pages": pages, "files": files, "errors": errors, "blocked": blocked}