"""
Website spider: crawl from a start URL with a depth limit and collect file links for the LinkGrabber.
"""

import posixpath
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse, urldefrag
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


def extract_links(html: str, base_url: str) -> list:
    parser = _LinkParser()
    parser.feed(html)
    seen = set()
    result = []
    for link in parser.links:
        full_url = urljoin(base_url, link)
        # Remove fragment
        full_url, _ = urldefrag(full_url)
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


FetchFn = Callable[[str], tuple]


def _extension(url: str) -> str:
    return posixpath.splitext(urlparse(url).path)[1].lower()


def crawl(start_url: str, fetch: FetchFn, options: SpiderOptions = None) -> dict:
    if options is None:
        options = SpiderOptions()
    
    extensions = tuple(ext.lower() for ext in options.extensions)
    
    pages = []
    files = []
    errors = []
    visited = set()
    queue = [(start_url, 0)]
    
    while queue:
        url, depth = queue.pop(0)
        
        # Skip if already visited (this prevents visiting URLs twice during crawling)
        if url in visited:
            continue
        
        # Stop when max_pages is reached
        if len(pages) >= options.max_pages:
            break
            
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
    
    return {"pages": pages, "files": files, "errors": errors}