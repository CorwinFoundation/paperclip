#!/usr/bin/env python3
"""Rebuild the pinned StrongREJECT conversation-case source bundle."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "third_party" / "oss-imports" / "strongreject"
COMMIT = "f7cad6c17e624e21d8df2278e918ae1dddb4cb56"
BASE = f"https://raw.githubusercontent.com/alexandrasouly/strongreject/{COMMIT}"
FILES = {
    "LICENSE": ("LICENSE", "e8bcbf075f0b236c00a8661f9d89189e2d3a8ab7c3d68c746d7801bb14c0f871"),
    "cases.csv": ("strongreject_dataset/strongreject_dataset.csv", "4dd70357e4ff8b5d0ba5ebafecab5d6dd5633ce8046e3dd1c8bd93e64de44381"),
    "judge-rubric.txt": ("strongreject/strongreject_evaluator_prompt.txt", "eecf618e80c94ffe8dd2044c8cdf42d232c3c253f833bb32fdb81387bf8968a8"),
}
PROHIBITED = re.compile(
    r"(?:minor|child|underage).{0,40}(?:sex|sexual|porn)|"
    r"(?:sex|sexual|porn).{0,40}(?:minor|child|underage)", re.I,
)
EXACT_DUPLICATE_PROMPT_SHA256 = {
    "9f7057b4676b5583ea38e321eeda7a7ccc7c25bb34169d07d51d422c7ec8e2db",
    "2afe1cd2b43b2417ed1a07e6b655894328c6efad1e97bf439ddd0792423e9b0c",
}


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
    prohibited = [row for row in rows if PROHIBITED.search(row["forbidden_prompt"])]
    duplicates = [
        row for row in rows
        if hashlib.sha256(row["forbidden_prompt"].encode()).hexdigest()
        in EXACT_DUPLICATE_PROMPT_SHA256
    ]
    kept = [row for row in rows if row not in prohibited and row not in duplicates]
    (OUT / "LICENSE").write_bytes(blobs["LICENSE"])
    (OUT / "judge-rubric.txt").write_bytes(blobs["judge-rubric.txt"])
    with (OUT / "cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(kept)
    (OUT / "PROVENANCE.json").write_text(json.dumps({
        "dataset": "StrongREJECT",
        "repository": "https://github.com/alexandrasouly/strongreject",
        "commit": COMMIT,
        "retrieved_at": "2026-10-08",
        "licence": "MIT",
        "source_rows": len(rows),
        "rows_imported": len(kept),
        "rows_dropped": {
            "prohibited_minor_sexual_content": len(prohibited),
            "exact_duplicate_prompt_sha256": sorted(EXACT_DUPLICATE_PROMPT_SHA256),
        },
        "source_sha256": {name: digest for name, (_, digest) in FILES.items()},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
