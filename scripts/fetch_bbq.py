#!/usr/bin/env python3
"""Fetch + stratify n=200 BBQ subset for ARE BIAS-FAIRNESS corpus pack.

Usage:
    python3 scripts/fetch_bbq.py [--out-dir corpus/bias/bbq] [--n 200] [--seed 42]

Downloads BBQ data from nyu-mll/BBQ GitHub (CC-BY-4.0 license).
Stratifies across 9 categories, 50/50 ambig/disambig, mixed polarity.
Writes:
  corpus/bias/bbq/bbq_subset_n{N}_seed{SEED}.jsonl
  corpus/bias/bbq/corpus.yaml  (manifest with sha256, source URLs, corpus_version)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import urllib.request
from pathlib import Path
from typing import Any

BBQ_CATEGORIES = [
    "Age",
    "Disability_status",
    "Gender_identity",
    "Nationality",
    "Physical_appearance",
    "Race_ethnicity",
    "Religion",
    "SES",
    "Sexual_orientation",
]

BBQ_GITHUB_BASE = (
    "https://raw.githubusercontent.com/nyu-mll/BBQ/main/data/{category}.jsonl"
)
BBQ_LICENSE = "CC-BY-4.0"
BBQ_CITATION = (
    "Parrish et al. 2022, BBQ: A hand-built bias benchmark for question answering, "
    "ACL Findings 2022. https://aclanthology.org/2022.findings-acl.165/"
)


def fetch_category(category: str) -> list[dict[str, Any]]:
    url = BBQ_GITHUB_BASE.format(category=category)
    req = urllib.request.Request(url, headers={"User-Agent": "ARE-scanner/corpus-fetch"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        lines = resp.read().decode("utf-8").splitlines()
    items = []
    for line in lines:
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def stratify(items: list[dict[str, Any]], n_per_cat: int, rng: random.Random) -> list[dict[str, Any]]:
    """Select n_per_cat items, balanced across context_condition (ambig/disambig)."""
    half = n_per_cat // 2
    ambig = [x for x in items if x.get("context_condition") == "ambig"]
    disambig = [x for x in items if x.get("context_condition") == "disambig"]
    rng.shuffle(ambig)
    rng.shuffle(disambig)
    selected = ambig[:half] + disambig[:n_per_cat - half]
    rng.shuffle(selected)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch BBQ corpus subset")
    parser.add_argument("--out-dir", default="corpus/bias/bbq")
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n_total = args.n
    n_cats = len(BBQ_CATEGORIES)
    base = n_total // n_cats
    remainder = n_total % n_cats

    rng = random.Random(args.seed)
    all_items: list[dict[str, Any]] = []
    source_urls: list[str] = []
    per_cat_counts: dict[str, int] = {}

    for i, cat in enumerate(BBQ_CATEGORIES):
        n_this = base + (1 if i < remainder else 0)
        url = BBQ_GITHUB_BASE.format(category=cat)
        print(f"  fetching {cat} (n={n_this}) from {url}", flush=True)
        raw = fetch_category(cat)
        selected = stratify(raw, n_this, rng)
        for item in selected:
            item["_are_category"] = cat
        all_items.extend(selected)
        source_urls.append(url)
        per_cat_counts[cat] = len(selected)
        print(f"    {cat}: {len(raw)} raw → {len(selected)} selected", flush=True)

    rng.shuffle(all_items)

    subset_file = out_dir / f"bbq_subset_n{n_total}_seed{args.seed}.jsonl"
    with open(subset_file, "w") as f:
        for item in all_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    digest = hashlib.sha256(subset_file.read_bytes()).hexdigest()

    context_cond_counts = {
        "ambig": sum(1 for x in all_items if x.get("context_condition") == "ambig"),
        "disambig": sum(1 for x in all_items if x.get("context_condition") == "disambig"),
    }
    polarity_counts = {
        "neg": sum(1 for x in all_items if x.get("question_polarity") == "neg"),
        "nonneg": sum(1 for x in all_items if x.get("question_polarity") == "nonneg"),
    }

    manifest = {
        "corpus_name": "bbq_subset",
        "corpus_version": f"n{n_total}_seed{args.seed}",
        "n_items": len(all_items),
        "seed": args.seed,
        "categories": BBQ_CATEGORIES,
        "per_category_counts": per_cat_counts,
        "context_condition_counts": context_cond_counts,
        "question_polarity_counts": polarity_counts,
        "subset_file": subset_file.name,
        "sha256": digest,
        "source": "nyu-mll/BBQ",
        "source_urls": source_urls,
        "license": BBQ_LICENSE,
        "citation": BBQ_CITATION,
        "fetch_args": {"n": n_total, "seed": args.seed},
        "notes": (
            "Carrier-rule compliant: no carrier names in probe items. "
            "Stratified 50/50 ambig/disambig per category. "
            "Seed=42 is the ARE-scanner canonical corpus seed."
        ),
    }

    import yaml  # local import so module works before pyyaml install
    manifest_file = out_dir / "corpus.yaml"
    with open(manifest_file, "w") as f:
        yaml.dump(manifest, f, sort_keys=False, allow_unicode=True)

    print(f"\nSubset written: {subset_file}")
    print(f"  n={len(all_items)}, sha256={digest[:16]}...")
    print(f"  context_condition: {context_cond_counts}")
    print(f"  polarity: {polarity_counts}")
    print(f"Manifest: {manifest_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
