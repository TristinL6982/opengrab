"""
LinkGrabber package grouping: derive package names from file names and group links.
"""

import re
from urllib.parse import urlsplit, unquote, parse_qs


def filename_from_url(url: str) -> str:
    """Extract filename from URL."""
    if url.lower().startswith("magnet:"):
        # For magnet links, extract the "dn" query parameter
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        dn_values = params.get('dn', [])
        if dn_values:
            return unquote(dn_values[0])
        else:
            return ""
    else:
        # For regular URLs, get the last segment of the path
        path = urlsplit(url).path
        if not path or path.endswith("/"):
            return ""
        return unquote(path.split("/")[-1])


MULTIPART_RES = [
    re.compile(r"\.part\d+\.rar$", re.IGNORECASE),
    re.compile(r"\.(?:7z|zip|rar)\.\d{3}$", re.IGNORECASE),
    re.compile(r"\.r\d{2}$", re.IGNORECASE),
    re.compile(r"\.z\d{2}$", re.IGNORECASE),
]


def package_name(filename: str) -> str:
    """Derive package name from filename."""
    if not filename:
        return filename
        
    # Check if it's a multipart file
    for regex in MULTIPART_RES:
        if regex.search(filename):
            # Remove the multipart suffix
            return regex.sub("", filename)
    
    # Remove only the last extension
    if "." in filename:
        last_dot_index = filename.rfind(".")
        if last_dot_index > 0:  # Make sure dot is not first character
            return filename[:last_dot_index]
    
    # If no extension or other rule matched, return as is
    return filename


def group_packages(urls) -> dict:
    """Group URLs by package name."""
    packages = {}
    
    for url in urls:
        filename = filename_from_url(url)
        pkg_name = package_name(filename)
        
        if not pkg_name:
            pkg_name = "Various"
            
        if pkg_name not in packages:
            packages[pkg_name] = []
        packages[pkg_name].append(url)
    
    return packages