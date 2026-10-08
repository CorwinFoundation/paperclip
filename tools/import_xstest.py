#!/usr/bin/env python3
"""Rebuild the pinned XSTest conversation-case source bundle."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "third_party" / "oss-imports" / "xstest"
COMMIT = "d7bb5bd738c1fcbc36edd83d5e7d1b71a3e2d84d"
BASE = f"https://raw.githubusercontent.com/paul-rottger/xstest/{COMMIT}"
FILES = {
    "LICENSE": ("LICENSE", "9e5f1b3c610b9c2da5c313bf81d577a7d1acec686bdb0384edefa6df0f90cd94"),
    "cases.csv": ("xstest_prompts.csv", "11783fb294ed017473ee53c207d71f2161c7672c8d0b037501e78387f801cb5a"),
}
PROHIBITED = re.compile(
    r"(?:minor|child|underage).{0,40}(?:sex|sexual|porn)|"
    r"(?:sex|sexual|porn).{0,40}(?:minor|child|underage)", re.I,
)
# Unsafe controls whose target-facing wording is >0.6 Jaccard-similar to the
# corresponding safe over-refusal row. Keep the safe row, which is XSTest's
# measurement target; provenance records every dropped source id.
NEAR_DUPLICATE_UNSAFE_IDS = {"35", "84", "129", "130", "138", "178", "189", "190", "310"}


def fetch(path: str, expected: str) -> bytes:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=30) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(f"sha256 mismatch for {path}: {actual}")
    return data


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    blobs = {name: fetch(path, digest) for name, (path, digest) in FILES.items()}
    rows = list(csv.DictReader(blobs["cases.csv"].decode("utf-8").splitlines()))
    prohibited = [row for row in rows if PROHIBITED.search(row["prompt"])]
    near_duplicates = [row for row in rows if row["id"] in NEAR_DUPLICATE_UNSAFE_IDS]
    kept = [row for row in rows if row not in prohibited and row not in near_duplicates]
    (OUT / "LICENSE").write_bytes(blobs["LICENSE"])
    with (OUT / "cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(kept)
    (OUT / "PROVENANCE.json").write_text(json.dumps({
        "dataset": "XSTest",
        "repository": "https://github.com/paul-rottger/xstest",
        "commit": COMMIT,
        "retrieved_at": "2026-10-08",
        "licence": "CC-BY-4.0",
        "source_rows": len(rows),
        "rows_imported": len(kept),
        "rows_dropped": {
            "prohibited_minor_sexual_content": len(prohibited),
            "near_duplicate_jaccard_gt_0_6": [row["id"] for row in near_duplicates],
        },
        "source_sha256": {name: digest for name, (_, digest) in FILES.items()},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
