#!/usr/bin/env python3
"""Rebuild the pinned CyberSecEval prompt-injection case bundle."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "third_party" / "oss-imports" / "cyberseceval"
COMMIT = "172c1074069eb88ec834124272c1b1c4f8893445"
BASE = f"https://raw.githubusercontent.com/meta-llama/PurpleLlama/{COMMIT}/CybersecurityBenchmarks"
FILES = {
    "LICENSE": ("LICENSE", "da6d3703ed11cbe42bd212c725957c98da23cbff1998c05fa4b3d976d1a58e93"),
    "cases.json": ("datasets/prompt_injection/prompt_injection.json", "069e4d5d36f6d19f972a3bbc65df840cc729354f89358a9031aeb44d95b18a9a"),
}
PROHIBITED = re.compile(
    r"(?:minor|child|underage).{0,40}(?:sex|sexual|porn)|"
    r"(?:sex|sexual|porn).{0,40}(?:minor|child|underage)", re.I,
)
NEAR_OR_EXACT_DUPLICATE_IDS = {
    94, 103, 104, 132, 146, 171, 172, 173, 174, 177, 178, 179, 184, 185,
    187, 191, 192, 194, 206, 208, 211, 212, 213, 214, 218, 220, 221, 222,
    223, 224, 226, 227, 229, 230, 231, 232, 233, 236, 241, 242, 243, 244,
    245, 247, 248, 250,
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
    rows = json.loads(blobs["cases.json"])
    prohibited = [
        row for row in rows
        if PROHIBITED.search(row["test_case_prompt"] + " " + row["user_input"])
    ]
    duplicates = [row for row in rows if row["prompt_id"] in NEAR_OR_EXACT_DUPLICATE_IDS]
    kept = [row for row in rows if row not in prohibited and row not in duplicates]
    (OUT / "LICENSE").write_bytes(blobs["LICENSE"])
    (OUT / "cases.json").write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "PROVENANCE.json").write_text(json.dumps({
        "dataset": "CyberSecEval prompt injection",
        "repository": "https://github.com/meta-llama/PurpleLlama",
        "commit": COMMIT,
        "retrieved_at": "2026-10-08",
        "licence": "MIT",
        "source_rows": len(rows),
        "rows_imported": len(kept),
        "rows_dropped": {
            "prohibited_minor_sexual_content": len(prohibited),
            "near_or_exact_duplicate_prompt_ids": sorted(NEAR_OR_EXACT_DUPLICATE_IDS),
        },
        "source_sha256": {name: digest for name, (_, digest) in FILES.items()},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
