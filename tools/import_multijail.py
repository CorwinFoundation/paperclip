#!/usr/bin/env python3
"""Rebuild the pinned MultiJail English/Arabic conversation-case bundle."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "third_party" / "oss-imports" / "multijail"
COMMIT = "577ac511437e07a256130945ef312a99158a18ad"
BASE = f"https://raw.githubusercontent.com/DAMO-NLP-SG/multilingual-safety-for-LLMs/{COMMIT}"
FILES = {
    "LICENSE": ("LICENSE", "1b995a4eb759b42d44d31bdf8cfa364c5eb404b3c0bda8c58ca4bd43788835e4"),
    "cases.csv": ("data/MultiJail.csv", "2798ed6fe470b80b1f486976e0fb3baa3ce008e62b6ca0abbf2e1ed5b1dfb355"),
}
PROHIBITED = re.compile(
    r"(?:minor|child|underage).{0,40}(?:sex|sexual|porn)|"
    r"(?:sex|sexual|porn).{0,40}(?:minor|child|underage)", re.I,
)
NEAR_OR_EXACT_DUPLICATE_IDS = {"9", "305", "306", "307", "312"}


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
    rows = list(csv.DictReader(blobs["cases.csv"].decode("utf-8-sig").splitlines()))
    prohibited = [row for row in rows if PROHIBITED.search(row["en"])]
    duplicates = [row for row in rows if row["id"] in NEAR_OR_EXACT_DUPLICATE_IDS]
    kept = [row for row in rows if row not in prohibited and row not in duplicates]
    (OUT / "LICENSE").write_bytes(blobs["LICENSE"])
    with (OUT / "cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(kept)
    (OUT / "PROVENANCE.json").write_text(json.dumps({
        "dataset": "MultiJail",
        "repository": "https://github.com/DAMO-NLP-SG/multilingual-safety-for-LLMs",
        "commit": COMMIT,
        "retrieved_at": "2026-10-08",
        "licence": "MIT",
        "source_rows": len(rows),
        "rows_imported": len(kept),
        "languages_imported": ["en", "ar"],
        "source_languages_available": ["en", "zh", "it", "vi", "ar", "ko", "th", "bn", "sw", "jv"],
        "requested_languages_unavailable": ["he", "es", "fr", "de", "pt"],
        "rows_dropped": {
            "prohibited_minor_sexual_content": len(prohibited),
            "near_or_exact_duplicate_ids": sorted(NEAR_OR_EXACT_DUPLICATE_IDS, key=int),
        },
        "source_sha256": {name: digest for name, (_, digest) in FILES.items()},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
