#!/usr/bin/env python3
"""Rebuild the BEAAA-34387 consumer build spec from its SPEC_SOURCE_PART comments.

The spec is not a file in this repo. It is eight base64 chunks in comments on
BEAAA-34387. Verifying the SHA256 is what makes the text quotable in QA findings.

    python3 tests/qa/reconstruct_spec_34387.py /tmp/spec.md
"""
import base64
import hashlib
import json
import os
import re
import sys
import urllib.request

EXPECTED = "0a096e6ae962176e88379a6d9e4a8afee838a66434f0b673137885dd6add3c46"
API = "http://127.0.0.1:3100"


def main(out):
    req = urllib.request.Request(
        f"{API}/api/issues/BEAAA-34387/comments?limit=60",
        headers={"Authorization": "Bearer " + os.environ["PAPERCLIP_API_KEY"]},
    )
    parts = {}
    for c in json.load(urllib.request.urlopen(req)):
        body = c.get("body") or ""
        m = re.match(r"SPEC_SOURCE_PART (\d)/8", body)
        if m:
            parts[int(m.group(1))] = body
    missing = [n for n in range(1, 9) if n not in parts]
    if missing:
        sys.exit(f"missing SPEC_SOURCE_PART {missing}")
    joined = "".join(parts[n] for n in sorted(parts))
    b64 = "".join(re.findall(r"DATA_BEGIN\n(.*?)\nDATA_END", joined, re.S)).replace("\n", "")
    raw = base64.b64decode(b64)
    got = hashlib.sha256(raw).hexdigest()
    if got != EXPECTED:
        sys.exit(f"sha256 mismatch: {got} != {EXPECTED}")
    with open(out, "wb") as fh:
        fh.write(raw)
    print(f"{out}: {len(raw)} bytes, sha256 {got} (matches)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/spec.md")
