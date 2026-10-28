import json
import os
import re
from typing import List, Dict, Any, Optional
import html
from urllib.parse import urljoin

from .base import SitePlugin, PluginError, GrabResult, ResolveResult, PluginContext


class TemplateError(ValueError):
    pass


ALLOWED_KEYS = {"id", "name", "site", "description", "url_pattern", "filename_regex", "size_regex", "direct_url_regex",
                "offline_markers", "headers"}
REQUIRED = {"id", "name", "site", "direct_url_regex"}


class DeclarativePlugin(SitePlugin):
    def __init__(self, template: dict):
        # Validate template
        if not isinstance(template, dict):
            raise TemplateError("template must be a dict")

        # Check for unknown keys
        unknown_keys = set(template.keys()) - ALLOWED_KEYS
        if unknown_keys:
            raise TemplateError(f"unknown key(s): {', '.join(unknown_keys)}")

        # Check for missing required keys
        missing_keys = REQUIRED - set(template.keys())
        if missing_keys:
            raise TemplateError(f"missing required key(s): {', '.join(missing_keys)}")

        # Validate types and values for required fields
        for key in REQUIRED:
            if not isinstance(template[key], str) or not template[key]:
                raise TemplateError(f"{key} must be a non-empty string")

        # Compile regexes
        self._url_pattern = None
        if "url_pattern" in template and template["url_pattern"]:
            try:
                self._url_pattern = re.compile(template["url_pattern"], re.I | re.S)
            except re.error as e:
                raise TemplateError(f"invalid url_pattern regex: {str(e)}")

        self._filename_regex = None
        if "filename_regex" in template and template["filename_regex"]:
            try:
                self._filename_regex = re.compile(template["filename_regex"], re.I | re.S)
            except re.error as e:
                raise TemplateError(f"invalid filename_regex regex: {str(e)}")

        self._size_regex = None
        if "size_regex" in template and template["size_regex"]:
            try:
                self._size_regex = re.compile(template["size_regex"], re.I | re.S)
            except re.error as e:
                raise TemplateError(f"invalid size_regex regex: {str(e)}")

        self._direct_url_regex = None
        if "direct_url_regex" in template and template["direct_url_regex"]:
            try:
                self._direct_url_regex = re.compile(template["direct_url_regex"], re.I | re.S)
            except re.error as e:
                raise TemplateError(f"invalid direct_url_regex regex: {str(e)}")

        # Set instance attributes
        self.id = template["id"]
        self.name = template["name"]
        self.site = template["site"].lower()
        self.description = template.get("description", "")
        self.origin = "template"

        # Handle offline markers (default to empty list)
        if "offline_markers" in template:
            if not isinstance(template["offline_markers"], list):
                raise TemplateError("offline_markers must be a list of strings")
            self.offline_markers = [str(m) for m in template["offline_markers"]]
        else:
            self.offline_markers = []

        # Handle headers (default to empty dict)
        if "headers" in template:
            if not isinstance(template["headers"], dict):
                raise TemplateError("headers must be a dict of string->string")
            self.headers = {str(k): str(v) for k, v in template["headers"].items()}
        else:
            self.headers = {}

    def supports(self, url: str) -> bool:
        return super().supports(url) and (
                not self._url_pattern or
                self._url_pattern.search(url)
        )

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        r = ctx.client.get(url)
        
        if r.status_code == 404:
            raise PluginError("offline: " + url)

        if self.offline_markers:
            lower_text = r.text.lower()
            if any(marker.lower() in lower_text for marker in self.offline_markers):
                raise PluginError("offline: " + url)

        # Extract filename
        filename = None
        if self._filename_regex:
            m = self._filename_regex.search(r.text)
            if m:
                filename = m.group(1).strip()
                filename = html.unescape(filename)

        if not filename:
            # Use last path segment as filename
            from urllib.parse import urlparse
            parsed_url = urlparse(r.url)
            filename = os.path.basename(parsed_url.path)

        # Extract size (digits only, remove commas and spaces)
        size = None
        if self._size_regex:
            m = self._size_regex.search(r.text)
            if m:
                size_str = m.group(1)
                # Remove commas and spaces
                size_clean = re.sub(r'[,\\s]', '', size_str)
                try:
                    size = int(size_clean)
                except ValueError:
                    pass

        # Extract direct URL
        direct_url = None
        if self._direct_url_regex:
            m = self._direct_url_regex.search(r.text)
            if m:
                direct_url = m.group(1)
                direct_url = html.unescape(direct_url)
                direct_url = urljoin(r.url, direct_url)

        return GrabResult(
            title=filename,
            filename=filename,
            size=size,
            site=self.site,
            plugin_id=self.id,
            direct_url=direct_url,
            direct_headers=dict(self.headers)
        )

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = self.grab(url, ctx)
        if g.direct_url is None:
            raise PluginError("no direct link: " + url)
        return ResolveResult(
            url=g.direct_url,
            filename=g.filename,
            size=g.size,
            headers=dict(self.headers)
        )


def load_templates_dir(path: str) -> tuple[List[DeclarativePlugin], Dict[str, str]]:
    """Load all JSON templates from a directory."""
    plugins = []
    errors = {}
    
    if not os.path.exists(path):
        return [], {}
    
    try:
        files = sorted(os.listdir(path))
    except OSError:
        # If we can't list the directory, treat as empty
        files = []
        
    seen_ids = set()

    for filename in files:
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(path, filename)
        try:
            with open(filepath, encoding="utf-8") as f:
                template = json.load(f)
                
            plugin = DeclarativePlugin(template)
            
            # Check for duplicate IDs
            if plugin.id in seen_ids:
                errors[filename] = "duplicate id"
                continue
                
            plugins.append(plugin)
            seen_ids.add(plugin.id)
            
        except (json.JSONDecodeError, TemplateError) as e:
            errors[filename] = str(e)
        except Exception as e:
            errors[filename] = str(e)

    return plugins, errors