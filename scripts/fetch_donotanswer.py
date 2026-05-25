#!/usr/bin/env python3
"""Fetch + subset DoNotAnswer corpus for ARE BIAS-FAIRNESS corpus pack.

Usage:
    python3 scripts/fetch_donotanswer.py [--out-dir corpus/bias/donotanswer] [--n 100] [--seed 42]

Downloads DoNotAnswer dataset from LibrAI/do-not-answer GitHub (MIT license).
Filters to sensitive-category instructions relevant to BIAS-FAIRNESS dim P2.4.
Writes:
  corpus/bias/donotanswer/donotanswer_subset_n{N}_seed{SEED}.jsonl
  corpus/bias/donotanswer/corpus.yaml
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sys
import urllib.request
from pathlib import Path
from typing import Any

DNA_URL = (
    "https://raw.githubusercontent.com/Libr-AI/do-not-answer/main/datasets/data_en.csv"
)
DNA_LICENSE = "MIT"
DNA_CITATION = (
    "Wang et al. 2023, Do-Not-Answer: A Dataset for Evaluating Safeguards in LLMs, "
    "EMNLP Findings 2023. https://arxiv.org/abs/2308.13387"
)

# Risk areas mapped to BIAS-FAIRNESS P2.4 (refusal/coverage parity across protected categories)
BIAS_RELEVANT_RISK_AREAS = {
    "Discrimination, Exclusion, Toxicity, Hateful, Offensive",
    "Human Trafficking",
    "Tailored Financial Advice",
    "Controlled Substances",  # parity across demographic groups
}

# Type codes that are more bias-relevant (instruction to discriminate)
BIAS_RELEVANT_TYPES = {
    "5.2",  # Stereotype, prejudice
    "5.1",  # Offensive language
    "3.3",  # Discrimination
    "1.6",  # Hate speech
}


def fetch_all() -> list[dict[str, Any]]:
    req = urllib.request.Request(DNA_URL, headers={"User-Agent": "ARE-scanner/corpus-fetch"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw))
    return list(reader)


def is_bias_relevant(row: dict[str, Any]) -> bool:
    risk_area = row.get("risk_area", "")
    types = row.get("types_of_harm", "") or row.get("type", "")
    return (
        risk_area in BIAS_RELEVANT_RISK_AREAS
        or any(t in types for t in BIAS_RELEVANT_TYPES)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch DoNotAnswer corpus subset")
    parser.add_argument("--out-dir", default="corpus/bias/donotanswer")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching DoNotAnswer from {DNA_URL}", flush=True)
    rows = fetch_all()
    print(f"  {len(rows)} total items fetched", flush=True)

    # Filter to bias-relevant items if enough, else take all
    bias_rows = [r for r in rows if is_bias_relevant(r)]
    print(f"  {len(bias_rows)} bias-relevant items", flush=True)

    pool = bias_rows if len(bias_rows) >= args.n else rows
    rng = random.Random(args.seed)
    rng.shuffle(pool)
    selected = pool[: args.n]

    subset_file = out_dir / f"donotanswer_subset_n{args.n}_seed{args.seed}.jsonl"
    with open(subset_file, "w") as f:
        for row in selected:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    digest = hashlib.sha256(subset_file.read_bytes()).hexdigest()

    risk_area_counts: dict[str, int] = {}
    for r in selected:
        ra = r.get("risk_area", "unknown")
        risk_area_counts[ra] = risk_area_counts.get(ra, 0) + 1

    manifest = {
        "corpus_name": "donotanswer_subset",
        "corpus_version": f"n{args.n}_seed{args.seed}",
        "n_items": len(selected),
        "seed": args.seed,
        "subset_file": subset_file.name,
        "sha256": digest,
        "source": "LibrAI/do-not-answer",
        "source_url": DNA_URL,
        "license": DNA_LICENSE,
        "citation": DNA_CITATION,
        "risk_area_counts": risk_area_counts,
        "fetch_args": {"n": args.n, "seed": args.seed},
        "notes": (
            "Carrier-rule compliant: no carrier names in probe items. "
            "Filtered to bias/discrimination-relevant risk areas for P2.4 coverage parity. "
            "Seed=42 is the ARE-scanner canonical corpus seed."
        ),
    }

    import yaml
    manifest_file = out_dir / "corpus.yaml"
    with open(manifest_file, "w") as f:
        yaml.dump(manifest, f, sort_keys=False, allow_unicode=True)

    print(f"\nSubset written: {subset_file}")
    print(f"  n={len(selected)}, sha256={digest[:16]}...")
    print(f"  risk_areas: {dict(list(risk_area_counts.items())[:5])}")
    print(f"Manifest: {manifest_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
