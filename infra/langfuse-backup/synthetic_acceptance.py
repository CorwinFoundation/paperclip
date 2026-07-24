#!/usr/bin/env python3
"""BEAAA-17701 bounded Langfuse acceptance client (synthetic data only)."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

PROHIBITED = re.compile(
    r"\b(customer|production|email|phone|address|ssn|social.security|credit.card|"
    r"patient|policyholder|insured|beneficiary|quote|bind|premium|limit|decline)\b",
    re.IGNORECASE,
)


def request(
    method: str,
    path: str,
    body: object | None,
    *,
    authorized: bool = True,
    transport_attempts: int = 1,
    transport_retry_seconds: float = 1,
):
    if transport_attempts < 1:
        raise ValueError("transport_attempts must be at least 1")
    endpoint = os.environ["LANGFUSE_ENDPOINT"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if authorized:
        token = base64.b64encode(
            f'{os.environ["LANGFUSE_PUBLIC_KEY"]}:{os.environ["LANGFUSE_SECRET_KEY"]}'.encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(endpoint + path, data=data, headers=headers, method=method)
    for attempt in range(transport_attempts):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except (urllib.error.URLError, ConnectionResetError, TimeoutError):
            if attempt + 1 == transport_attempts:
                raise
            time.sleep(transport_retry_seconds)
    raise AssertionError("unreachable")


def main() -> int:
    trace_id = str(uuid.uuid4())
    score_id = str(uuid.uuid4())
    synthetic = {
        "id": trace_id,
        "name": "beaaa-17701-synthetic-acceptance",
        "userId": "synthetic-actor-001",
        "sessionId": "synthetic-session-001",
        "metadata": {
            "data_class": "synthetic",
            "ticket": "BEAAA-17701",
            "retention_end": "2026-07-29T23:59:59Z",
        },
        "input": {"prompt": "Return the fixed token SYNTHETIC_OK."},
        "output": {"text": "SYNTHETIC_OK"},
    }
    encoded = json.dumps(synthetic, separators=(",", ":"))
    if PROHIBITED.search(encoded):
        raise RuntimeError("synthetic payload contains a prohibited field or decision term")

    status, _ = request(
        "POST",
        "/api/public/ingestion",
        {"batch": [{"id": str(uuid.uuid4()), "type": "trace-create", "timestamp": "2026-07-24T00:00:00Z", "body": synthetic}]},
        transport_attempts=60,
    )
    if status not in (200, 207):
        raise RuntimeError(f"trace ingest failed: HTTP {status}")

    for _ in range(20):
        status, trace = request("GET", f"/api/public/traces/{trace_id}", None)
        if status == 200:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"trace retrieval failed: HTTP {status}")
    if trace.get("metadata", {}).get("data_class") != "synthetic":
        raise RuntimeError("retrieved trace lost synthetic data classification")

    status, _ = request(
        "POST",
        "/api/public/scores",
        {"id": score_id, "traceId": trace_id, "name": "eventual-outcome", "value": 1, "comment": "synthetic feedback"},
    )
    if status not in (200, 201):
        raise RuntimeError(f"feedback attach failed: HTTP {status}")
    status, scores = request("GET", f"/api/public/scores?traceId={trace_id}", None)
    if status != 200 or score_id not in json.dumps(scores):
        raise RuntimeError("feedback read failed")

    unauthorized, _ = request("GET", f"/api/public/traces/{trace_id}", None, authorized=False)
    if unauthorized not in (401, 403):
        raise RuntimeError(f"unauthorized request was not rejected: HTTP {unauthorized}")

    print(json.dumps({"result": "PASS", "trace_id": trace_id, "score_id": score_id, "unauthorized_http": unauthorized}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
