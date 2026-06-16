from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

from .response_limits import OversizedResponseError, ensure_under_response_cap_bytes, max_response_bytes


@dataclass
class EgressAllowlist:
    portal_base_url: str
    agent_endpoint: str | None = None

    def assert_allowed(self, url: str) -> None:
        target = _origin(url)
        allowed = {_origin(self.portal_base_url)}
        if self.agent_endpoint:
            allowed.add(_origin(self.agent_endpoint))
        if target not in allowed:
            raise PermissionError(f"egress blocked for origin {target}")


class PortalClient:
    def __init__(self, portal_base_url: str, allowlist: EgressAllowlist, bearer_token: str | None = None):
        self.portal_base_url = portal_base_url.rstrip("/") + "/"
        self.allowlist = allowlist
        self.session = requests.Session()
        self.bearer_token = bearer_token

    def enroll(self, payload: dict) -> dict:
        return self._request("POST", "connectors/enroll", json=payload).json()

    def accept_invite(self, payload: dict) -> dict:
        return self._request("POST", "dp/accept", data=payload).json()

    def next_task(self, scan_id: str, connector_id: str | None = None) -> requests.Response:
        headers = {"x-connector-id": connector_id} if connector_id else {}
        return self._request("GET", f"scans/{scan_id}/tasks/next", headers=headers, timeout=65)

    def post_envelope(self, scan_id: str, envelope: dict) -> dict:
        return self._request("POST", f"scans/{scan_id}/envelopes", json=envelope).json()

    def post_redacted_record(self, record: dict) -> dict:
        idempotency_key = f"{record['connector_id']}:{record['agent_call_id']}"
        return self._request(
            "POST",
            "telemetry/redacted-record",
            json=record,
            headers={"Idempotency-Key": idempotency_key},
        ).json()

    def complete(self, scan_id: str) -> dict:
        return self._request("POST", f"scans/{scan_id}/complete", json={}).json()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = urljoin(self.portal_base_url, path)
        self.allowlist.assert_allowed(url)
        headers = kwargs.pop("headers", {})
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        response = self.session.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response


def call_agent(endpoint: str, payload: dict, allowlist: EgressAllowlist, timeout: int) -> dict:
    allowlist.assert_allowed(endpoint)
    response = requests.post(endpoint, json=payload, timeout=timeout, stream=True)
    response.raise_for_status()
    body = _read_capped_response_body(response)
    try:
        return response.json() if getattr(response, "_content", None) == body else _json_loads(body)
    except ValueError:
        return {"response": body.decode(response.encoding or "utf-8", errors="replace")}


def _read_capped_response_body(response: requests.Response) -> bytes:
    cap = max_response_bytes()
    chunks: list[bytes] = []
    observed = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        observed += len(chunk)
        if observed > cap:
            raise OversizedResponseError(observed, cap)
        chunks.append(chunk)
    body = b"".join(chunks)
    response._content = ensure_under_response_cap_bytes(body, cap)
    response._content_consumed = True
    return body


def _json_loads(body: bytes) -> dict:
    import json

    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise ValueError("agent response JSON must be an object")
    return decoded


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid egress URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}".lower()
