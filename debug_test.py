#!/usr/bin/env python

import sys
import os
sys.path.insert(0, '/c/Users/trist/opengrab/src')

# Import everything we need for reproduction 
from opengrab.spider import extract_links, crawl, SpiderOptions, _LinkParser
from urllib.parse import urlparse
import re

def manual_debug_crawl():
    print("=== Manual Debug Test ===")
    
    # Create fake fetch mapping exactly like in test
    fake_fetch = {
        "https://s.com/": (
            "text/html",
            '<a href="https://other.com/page">other</a><a href="https://cdn.other.com/x.zip">zip</a>',
        ),
    }
    
    def fetch(url):
        if url not in fake_fetch:
            raise ConnectionError("Not found")
        return fake_fetch[url]

    print("Fetching https://s.com/ with same_domain=False:")
    result = crawl(
        "https://s.com/",
        fetch,
        SpiderOptions(extensions=(".zip",), same_domain=False),
    )
    
    print("Result pages:", result["pages"])
    print("Result files:", result["files"])
    print("Result errors:", result["errors"])

if __name__ == "__main__":
    manual_debug_crawl()