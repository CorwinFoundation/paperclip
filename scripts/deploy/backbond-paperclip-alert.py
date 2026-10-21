#!/usr/bin/env python3
"""Send a minimal systemd failure notice to the BackBond Paperclip company."""

from __future__ import annotations

import datetime as dt
import json
import socket
import sys
import urllib.error
import urllib.request


PAPERCLIP_COMMENT_URL = (
    "http://127.0.0.1:18100/api/issues/BEAAA-18049/comments"
)
ALLOWED_UNITS = {
    "backbond-scanner-live.service",
    "are-scanner-scanlog-backup.service",
    "are-scanner-scanlog-restoretest.service",
    "beaaa18102-alert-route-test.service",
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ALLOWED_UNITS:
        print("paperclip-alert: rejected unapproved unit", file=sys.stderr)
        return 64

    unit = sys.argv[1]
    observed_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    test_prefix = (
        "NON-LIVE ROUTE TEST"
        if unit.startswith("beaaa18102-")
        else "PRODUCTION INCIDENT"
    )
    body = (
        f"{test_prefix}: systemd OnFailure reported `{unit}` on "
        f"`{socket.gethostname()}` at `{observed_at}`. "
        "This automated notice contains no service logs, credentials, customer data, "
        "database content, or Scanner evidence. The company operator should inspect "
        "the host through the approved root-custodied path."
    )

    def post(resume: bool) -> int:
        payload = json.dumps({"body": body, "resume": resume}).encode("utf-8")
        request = urllib.request.Request(
            (
                "http://127.0.0.1:18100/api/issues/BEAAA-42989/comments"
                if unit == "backbond-scanner-live.service"
                else PAPERCLIP_COMMENT_URL
            ),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status

    try:
        status = post(resume=True)
    except urllib.error.HTTPError as error:
        if error.code != 409:
            print("paperclip-alert: delivery rejected", file=sys.stderr)
            return 1
        try:
            status = post(resume=False)
        except (urllib.error.URLError, TimeoutError):
            print("paperclip-alert: fallback delivery failed", file=sys.stderr)
            return 1
    except (urllib.error.URLError, TimeoutError):
        print("paperclip-alert: delivery failed", file=sys.stderr)
        return 1

    if status < 200 or status >= 300:
        print(f"paperclip-alert: unexpected HTTP status {status}", file=sys.stderr)
        return 1
    print(f"paperclip-alert: delivered unit={unit} status={status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
