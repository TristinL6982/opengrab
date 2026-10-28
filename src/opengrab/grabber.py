"""
LinkGrabber analyzer: pure functions that turn pasted text or a container file into deduped, grouped packages.
"""

from opengrab.linkgrab import extract_urls, dedupe
from opengrab.packages import group_packages
from opengrab.containers import detect_container, decode_rsdf, ContainerError


def analyze_urls(urls, existing=()) -> dict:
    """Analyze a list of URLs, returning deduped and grouped packages."""
    urls = dedupe(urls, existing)
    packages = group_packages(urls)
    
    # Convert to the required output format
    result_packages = []
    for name in sorted(packages.keys()):
        result_packages.append({"name": name, "links": packages[name]})
    
    return {"count": len(urls), "packages": result_packages}


def analyze_text(text: str, existing=()) -> dict:
    """Analyze text input, extracting URLs and then analyzing them."""
    return analyze_urls(extract_urls(text or ""), existing)


def analyze_container(filename: str, data: bytes, existing=()) -> dict:
    """Analyze container file (RSDF, DLC, CCF)."""
    kind = detect_container(filename)
    if kind == "rsdf":
        return analyze_urls(decode_rsdf(data), existing)
    if kind is None:
        raise ContainerError("not a container: " + filename)
    raise ContainerError(kind + " containers are not supported yet")