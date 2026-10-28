import json
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Clearance:
    host: str
    cookies: Dict[str, str]
    user_agent: str
    expires_at: float

    def is_expired(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        # Returns True if the clearance has expired (with 30s margin)
        return now >= self.expires_at - 30


class ClearanceStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._clearances: Dict[str, Clearance] = {}
        
        if path and os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    for host, clearance_data in data.items():
                        self._clearances[host] = Clearance(
                            host=clearance_data["host"],
                            cookies=clearance_data["cookies"],
                            user_agent=clearance_data["user_agent"],
                            expires_at=clearance_data["expires_at"]
                        )
            except (json.JSONDecodeError, KeyError):
                # Corrupt or invalid JSON file - start with empty store
                pass

    def put(self, clearance: Clearance) -> None:
        normalized_host = self._normalize_host(clearance.host)
        # Store it under normalized key but keep the original host for return purposes
        self._clearances[normalized_host] = clearance
        
        if self.path:
            self._save_to_disk()

    def _save_to_disk(self) -> None:
        if self.path:
            # Write atomically
            temp_path = f"{self.path}.tmp"
            try:
                with open(temp_path, "w") as f:
                    json.dump(
                        {
                            host: {
                                "host": c.host,
                                "cookies": c.cookies,
                                "user_agent": c.user_agent,
                                "expires_at": c.expires_at
                            }
                            for host, c in self._clearances.items()
                        },
                        f
                    )
                os.replace(temp_path, self.path)
            except OSError:
                # If atomic write fails, continue without saving
                pass

    def get(self, host: str, now: Optional[float] = None) -> Optional[Clearance]:
        if now is None:
            now = time.time()
            
        normalized_host = self._normalize_host(host)
        
        # Try exact match first
        if normalized_host in self._clearances:
            clearance = self._clearances[normalized_host]
            if not clearance.is_expired(now):
                return clearance
            else:
                # Remove expired entry and return None
                del self._clearances[normalized_host]
                if self.path:
                    self._save_to_disk()
                return None
                
        # Try parent domains
        parts = normalized_host.split(".")
        if len(parts) >= 3:
            for i in range(2, len(parts)):
                parent_host = ".".join(parts[i:])
                if parent_host in self._clearances:
                    clearance = self._clearances[parent_host]
                    if not clearance.is_expired(now):
                        return clearance
                    else:
                        # Remove expired entry and return None
                        del self._clearances[parent_host]
                        if self.path:
                            self._save_to_disk()
                        return None
                        
        return None

    def remove(self, host: str) -> None:
        normalized_host = self._normalize_host(host)
        if normalized_host in self._clearances:
            del self._clearances[normalized_host]
            if self.path:
                self._save_to_disk()

    def clear(self) -> None:
        self._clearances.clear()
        if self.path:
            self._save_to_disk()

    def __len__(self) -> int:
        return len(self._clearances)

    @staticmethod
    def _normalize_host(host: str) -> str:
        # Convert to lowercase
        host = host.lower()
        
        # Remove port if present
        if ":" in host:
            host = host.split(":", 1)[0]
            
        # Remove www. prefix if present
        if host.startswith("www."):
            host = host[4:]
            
        return host

    def apply(self, host: str, headers: Dict[str, str], now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
            
        clearance = self.get(host, now)
        if clearance is None:
            return False
            
        # Set User-Agent
        headers["User-Agent"] = clearance.user_agent
        
        # Merge cookies
        cookie_header = headers.get("Cookie", "")
        existing_cookies = {}
        if cookie_header:
            for cookie in cookie_header.split(";"):
                cookie = cookie.strip()
                if "=" in cookie:
                    name, value = cookie.split("=", 1)
                    existing_cookies[name] = value
                    
        # Add clearance cookies, replacing existing ones with same names
        for cookie_name, cookie_value in clearance.cookies.items():
            existing_cookies[cookie_name] = cookie_value
            
        # Format merged cookies back into string
        merged_cookies = "; ".join(f"{name}={value}" for name, value in existing_cookies.items())
        headers["Cookie"] = merged_cookies
        
        return True


def from_browser_cookies(host: str, cookies: List[Dict], user_agent: str, now: Optional[float] = None) -> Optional[Clearance]:
    """Build a Clearance from browser cookies; None unless a cf_clearance cookie is present."""
    if now is None:
        now = time.time()
    kept: Dict[str, str] = {}
    clearance_cookie = None
    for cookie in cookies:
        name = cookie.get("name", "")
        if name == "cf_clearance" or name.startswith("__cf") or name == "__cfduid":
            kept[name] = cookie.get("value", "")
            if name == "cf_clearance":
                clearance_cookie = cookie
    if clearance_cookie is None:
        return None
    try:
        expires = float(clearance_cookie.get("expires", -1))
    except (TypeError, ValueError):
        expires = -1.0
    expires_at = expires if expires > 0 else now + 1800
    return Clearance(host=host, cookies=kept, user_agent=user_agent, expires_at=expires_at)
