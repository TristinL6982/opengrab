"""
Plugin marketplace catalog: listings with ratings, search, and verified installs.
"""

import hashlib
from dataclasses import dataclass, field
from opengrab.plugins.permissions import Permissions, parse_permissions
from opengrab.plugins.updates import version_tuple


class MarketplaceError(Exception):
    pass


@dataclass
class Listing:
    id: str
    name: str
    version: str
    url: str
    sha256: str
    author: str = ""
    description: str = ""
    rating: float = 0.0
    ratings_count: int = 0
    signature: str = ""
    permissions: Permissions = field(default_factory=Permissions)


def parse_listing(d: dict) -> Listing:
    required_fields = ["id", "name", "version", "url", "sha256"]
    for key in required_fields:
        if key not in d or not d[key]:
            raise MarketplaceError(f"listing missing field: {key}")
    
    rating = float(d.get("rating", 0.0))
    if rating < 0 or rating > 5:
        raise MarketplaceError("rating out of range")
    
    ratings_count = int(d.get("ratings_count", 0))
    permissions = parse_permissions(d.get("permissions", {}))
    
    return Listing(
        id=d["id"],
        name=d["name"],
        version=d["version"],
        url=d["url"],
        sha256=d["sha256"].lower(),
        author=d.get("author", ""),
        description=d.get("description", ""),
        rating=rating,
        ratings_count=ratings_count,
        signature=d.get("signature", ""),
        permissions=permissions
    )


def parse_catalog(data: dict) -> list:
    listings = []
    seen_ids = set()
    
    for item in data.get("plugins", []):
        listing = parse_listing(item)
        if listing.id in seen_ids:
            raise MarketplaceError(f"duplicate listing id: {listing.id}")
        seen_ids.add(listing.id)
        listings.append(listing)
    
    return listings


def search(listings, query: str = "", sort: str = "rating") -> list:
    # Filter listings based on query
    filtered = []
    query_lower = query.strip().lower()
    
    for listing in listings:
        if (not query_lower or 
            query_lower in listing.id.lower() or 
            query_lower in listing.name.lower() or 
            query_lower in listing.description.lower()):
            filtered.append(listing)
    
    # Sort based on sort parameter
    if sort == "rating":
        filtered.sort(key=lambda x: (-x.rating, -x.ratings_count, x.id))
    elif sort == "name":
        filtered.sort(key=lambda x: (x.name.lower(), x.id))
    else:
        raise MarketplaceError(f"unknown sort: {sort}")
    
    return filtered


def updates_available(listings, installed: dict) -> list:
    result = []
    
    for listing in listings:
        if listing.id in installed:
            if version_tuple(listing.version) > version_tuple(installed[listing.id]):
                result.append(listing)
    
    return result


def verify_download(listing: Listing, data: bytes, verifier=None) -> None:
    if hashlib.sha256(data).hexdigest() != listing.sha256:
        raise MarketplaceError(f"sha256 mismatch for {listing.id}")
    
    if verifier is not None:
        if not listing.signature:
            raise MarketplaceError(f"unsigned plugin: {listing.id}")
        if not verifier(listing, data):
            raise MarketplaceError(f"bad signature for {listing.id}")