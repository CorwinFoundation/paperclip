"""Loopback-only, GET-only Paperclip corroboration reader.

This process is the only component allowed to hold PAPERCLIP_API_KEY. It must
run co-located with Paperclip and must never be exposed through Cloudflare.
"""

from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_PATHS = (
    re.compile(r"^/api/issues/[0-9a-f-]+$"),
    re.compile(r"^/api/issues/[0-9a-f-]+/heartbeat-context$"),
    re.compile(r"^/api/issues/[0-9a-f-]+/comments$"),
    re.compile(r"^/api/agents/[0-9a-f-]+$"),
)


class PaperclipReader:
    def __init__(self, base_url: str, api_key: str, timeout: float = 3.0):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid Paperclip URL")
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if not loopback:
            raise ValueError("Paperclip reader requires a loopback Paperclip URL")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def get(self, path: str):
        if not any(pattern.fullmatch(path) for pattern in ALLOWED_PATHS):
            raise ValueError("Paperclip endpoint is outside the GET-only allowlist")
        request = Request(self.base_url + path, method="GET")
        request.add_header("Authorization", f"Bearer {self.api_key}")
        with urlopen(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"Paperclip returned {response.status}")
            return json.load(response)

