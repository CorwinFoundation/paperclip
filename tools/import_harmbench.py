#!/usr/bin/env python3
"""Rebuild the pinned HarmBench text-only conversation-case bundle."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "third_party" / "oss-imports" / "harmbench"
COMMIT = "8e1604d1171fe8a48d8febecd22f600e462bdcdd"
BASE = f"https://raw.githubusercontent.com/centerforaisafety/HarmBench/{COMMIT}"
FILES = {
    "LICENSE": ("LICENSE", "fe29c68dd1216e81e98a6069165e1c720757b23bbcaa6c0cb8f9a957bdaaca80"),
    "cases.csv": ("data/behavior_datasets/harmbench_behaviors_text_all.csv", "8d81accedd38eaaf8b760618622bb888417d1fd0c86eba65c427a16f1cbb4afc"),
}
PROHIBITED = re.compile(
    r"(?:minor|child|underage).{0,40}(?:sex|sexual|porn)|"
    r"(?:sex|sexual|porn).{0,40}(?:minor|child|underage)", re.I,
)
NEAR_OR_EXACT_DUPLICATE_IDS = {
    "sexist_email_female_colleague",
    "cryptojacking_computer_hijack_script",
    "atm_hacking_instructions",
    "children_laundry_detergent_ads",
    "stalking_promotion_blog_post",
    "blackmail_explicit_images_guide",
    "september_11_hoax_article",
    "holocaust_denial_article",
    "5g_covid19_link_argument",
    "vaccines_autism_twitter_thread",
    "explicit_pornographic_story",
    "secretary_explicit_images_coercion",
    "chop_shops_sao_paulo_locating",
    "bleach_vinegar_mixing_enticement",
    "bleach_rubbing_alcohol_mixing_enticement",
    "tech_support_scam_phone_script",
    "tax_fraud_instructions",
    "nyc_mass_killing_instructions",
    "parathion_synthesis_instructions",
    "lsd_25_nitrogen_stream_size",
    "sarin_precursors_obtaining_instructions",
    "mustard_gas_synthesis_home_advice",
    "vx_synthesis_stepbystep_proportions",
    "morphine_homebake_detailed_instructions",
    "heroin_synthesis_high_school",
    "cocaine_synthesis_detailed_instructions",
    "fentanyl_synthesis_stepbystep_instructions_overview",
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
    eligible = [row for row in rows if row["FunctionalCategory"] in {"standard", "contextual"}]
    prohibited = [
        row for row in eligible
        if PROHIBITED.search(row["Behavior"] + " " + row.get("ContextString", ""))
    ]
    duplicates = [row for row in eligible if row["BehaviorID"] in NEAR_OR_EXACT_DUPLICATE_IDS]
    kept = [row for row in eligible if row not in prohibited and row not in duplicates]
    (OUT / "LICENSE").write_bytes(blobs["LICENSE"])
    with (OUT / "cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(kept)
    (OUT / "PROVENANCE.json").write_text(json.dumps({
        "dataset": "HarmBench text behaviors",
        "repository": "https://github.com/centerforaisafety/HarmBench",
        "commit": COMMIT,
        "retrieved_at": "2026-10-08",
        "licence": "MIT",
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "rows_imported": len(kept),
        "rows_dropped": {
            "copyright": sum(row["FunctionalCategory"] == "copyright" for row in rows),
            "prohibited_minor_sexual_content": len(prohibited),
            "near_or_exact_duplicate_behavior_ids": sorted(NEAR_OR_EXACT_DUPLICATE_IDS),
        },
        "source_sha256": {name: digest for name, (_, digest) in FILES.items()},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
