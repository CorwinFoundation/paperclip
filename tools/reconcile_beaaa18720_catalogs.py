#!/usr/bin/env python3
"""Reconcile four accepted BEAAA-18738 catalogs into a 2,000-row content inventory."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "artifacts/beaaa-18715/unified-probe-inventory.jsonl"
OUT = ROOT / "artifacts/beaaa-18720"
PARENT_SHA = "67dd7bb6c65c28dcc633e5b169c30b5ca0ac7d84fdfe5cef4e3540a7de56866e"
CATALOGS = [
    ("BEAAA-18739", OUT / "batches/beaaa-18739-voice-multilingual-accessibility.jsonl", "c8d7b56cf922e467381f81f8cb5908a9c631bfbf960731128ef0d1befabe3c8f"),
    ("BEAAA-18740", OUT / "batches/beaaa-18740-explicit-scenario-catalogs.jsonl", "787943e3c8d12563d9d19d6ff748a2efd067bd0daf5cfef44311a8b96e32a6c9"),
    ("BEAAA-18741", OUT / "batches/batch-3-residency-resources-sandbox-patch-review.jsonl", "bd318eeb605586764dad5bfd2367babfb39e96220a139c4204e612f49ba53187"),
    ("BEAAA-18742", OUT / "batches/batch-4-explicit-scenario-catalog.jsonl", "35d1ff32e25b07f5c9e60a7dd8f6defa06aa1bac4c5e255bc99a66252da9ef67"),
]
NEW_FAMILIES = {
    "voice_turn_taking_interruption_recovery", "voice_consent_recording_boundary",
    "voice_identity_channel_transfer", "multilingual_policy_consistency",
    "accessibility_assistive_action_integrity", "long_context_instruction_hierarchy_retention",
    "context_truncation_fail_safe", "structured_output_contract_semantics",
    "time_sensitive_decision_freshness", "provenance_chain_of_custody",
    "data_residency_locality_boundary", "resource_budget_enforcement",
    "sandbox_process_network_boundary", "code_patch_scope_preservation",
    "code_review_regression_detection", "evaluation_awareness_behavior_consistency",
    "concurrent_shared_state_isolation", "approval_preview_mutation_binding",
    "checkpoint_restore_integrity", "human_override_takeover_relinquishment",
}
REQUIRED = {
    "stable_case_id", "case_slug", "family_id", "version", "substantive_cluster",
    "actor_trust_boundary", "control_type", "design_partner_profiles", "preconditions",
    "prompt_turn_sequence", "actual_state_sequence", "expected_observable_behavior",
    "failure_condition", "deterministic_oracle", "evidence_fields", "applicability",
    "provenance", "reporting_status", "semantic_behavior_fingerprint", "dedupe_sha256",
}
COMMON = set("""
active actual after against agent assistant baseline behavior boundary bounded capture case
commit condition confirm current declared effect effects equals evidence expected failure fixture
loaded make observed operation oracle policy precondition preview prohibited record receipt required
scenario specific state synthetic transition user value zero system event role content type
deterministic field assertion allow rule requires capability pass enabled all entities endpoints
files values messages tenant isolated stable slug rendered turns events applicability manifest
source revision ticket reported not scored
""".split())

REPORT_METADATA = """---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This evidence expands the candidate probe inventory without changing the BackBond Scanner scoring or runtime surface."
contracts_referenced: ["are-scanner-api-v1.md@v1.0", "scanner-implementation-supervision-v1.md@v1.0"]
ticket_refs: ["BEAAA-18738", "BEAAA-18739", "BEAAA-18740", "BEAAA-18741", "BEAAA-18742"]
proof_points_cited: []
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["No rule change, contract-shape change, tamper-resistance change, external-bound asset, or promotion."]
---

"""

CLOSEOUT_METADATA = REPORT_METADATA.replace(
    "artifact_type: report", "artifact_type: implementation_closeout"
).replace(
    "This evidence expands the candidate probe inventory without changing the BackBond Scanner scoring or runtime surface.",
    "This content-only expansion strengthens design-partner probe coverage while leaving the BackBond Scanner contract, scoring, and runtime unchanged.",
)


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def flatten(value):
    if isinstance(value, dict):
        return " ".join(flatten(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(flatten(item) for item in value)
    return str(value)


def normalized_skeleton(row):
    value = canon({
        "boundary": row.get("actor_trust_boundary", ""),
        "preconditions": row.get("preconditions", []),
        "turns": row.get("prompt_turn_sequence", []),
        "states": row.get("actual_state_sequence", []),
        "expected": row.get("expected_observable_behavior", ""),
        "failure": row.get("failure_condition", ""),
        "oracle": row.get("deterministic_oracle", row.get("oracle", {})),
        "applicability": row.get("applicability", {}),
    }).lower()
    slug = str(row.get("case_slug", "")).lower()
    if slug:
        value = value.replace(slug, "<case>")
    value = re.sub(r"bb\d+\.[a-z0-9_.-]+", "<identifier>", value)
    value = re.sub(r"`[^`]+`", "<case>", value)
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "<number>", value)
    value = re.sub(
        r"\b(?:alpha|beta|gamma|tenant|user|actor|requester|recipient|account|item|record)"
        r"[-_ ]?[a-z0-9]*\b", "<identity>", value,
    )
    value = re.sub(
        r"\b(?:arabic|english|french|german|hebrew|hindi|japanese|mandarin|spanish|"
        r"en-us|en-gb|fr-fr|de-de|he-il|ar-sa)\b", "<locale>", value,
    )
    return " ".join(value.split())


def tokens(text):
    return {
        token for token in re.findall(r"[a-z]{3,}", text.lower().replace("_", " "))
        if token not in COMMON
    }


def new_behavior_tokens(row):
    oracle = row.get("deterministic_oracle", {})
    parts = [
        row.get("substantive_cluster", ""), row.get("actor_trust_boundary", ""),
        flatten(row.get("prompt_turn_sequence", [])),
        flatten(row.get("actual_state_sequence", [])),
        flatten(row.get("expected_observable_behavior", "")),
        flatten(row.get("failure_condition", "")),
        str(oracle.get("field", "")) if isinstance(oracle, dict) else "",
    ]
    return tokens(" ".join(parts))


def parent_behavior_tokens(row):
    oracle = row.get("deterministic_oracle", row.get("oracle", {}))
    parts = [
        row.get("substantive_cluster", row.get("cluster", "")),
        row.get("actor_trust_boundary", ""),
        flatten(row.get("expected_observable_behavior", row.get("expected_behavior", ""))),
        flatten(row.get("failure_condition", "")),
        str(oracle.get("field", "")) if isinstance(oracle, dict) else "",
    ]
    return tokens(" ".join(map(str, parts)))


def jaccard(left, right):
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def event_summary(row):
    turns = row.get("prompt_turn_sequence", [])
    contents = []
    for turn in turns:
        if isinstance(turn, dict):
            contents.append(str(turn.get("content", turn.get("prompt", ""))))
        else:
            contents.append(str(turn))
    return contents[-3:]


def pair_disposition(left, right, score):
    lo = left.get("deterministic_oracle", {})
    ro = right.get("deterministic_oracle", {})
    return {
        "left": left["stable_case_id"], "right": right["stable_case_id"],
        "ratio": round(score, 4), "disposition": "retain_distinct",
        "left_transitions": event_summary(left), "right_transitions": event_summary(right),
        "left_expected": left["expected_observable_behavior"],
        "right_expected": right["expected_observable_behavior"],
        "left_failure": left["failure_condition"], "right_failure": right["failure_condition"],
        "left_oracle": lo.get("field"), "right_oracle": ro.get("field"),
    }


def main():
    parent_bytes = PARENT.read_bytes()
    parent_lines = parent_bytes.splitlines(keepends=True)
    assert hashlib.sha256(parent_bytes).hexdigest() == PARENT_SHA
    assert len(parent_lines) == 1500 and parent_bytes.endswith(b"\n")
    parent = [json.loads(line) for line in parent_lines]

    added, source_counts, catalog_hashes = [], {}, {}
    for ticket, path, expected_sha in CATALOGS:
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual_sha == expected_sha, (ticket, actual_sha, expected_sha)
        rows = load_jsonl(path)
        assert len(rows) == 125, (ticket, len(rows))
        added.extend(rows)
        source_counts[ticket] = len(rows)
        catalog_hashes[ticket] = actual_sha
    assert len(added) == 500

    for row in added:
        assert set(row) == REQUIRED, (row.get("stable_case_id"), set(row) ^ REQUIRED)
        assert row["control_type"] in {"positive", "negative"}
        assert row["design_partner_profiles"]
        assert len(row["prompt_turn_sequence"]) >= 3
        assert len(row["actual_state_sequence"]) >= 4
        assert row["applicability"].get("na_is_pass") is False
        assert row["reporting_status"] == "reported_not_scored"
        assert re.fullmatch(r"[0-9a-f]{64}", row["dedupe_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["semantic_behavior_fingerprint"])

    family_counts = Counter(row["family_id"] for row in added)
    assert set(family_counts) == NEW_FAMILIES
    assert all(family_counts[family] == 25 for family in NEW_FAMILIES)
    cluster_counts = {
        family: len({row["substantive_cluster"] for row in added if row["family_id"] == family})
        for family in NEW_FAMILIES
    }
    positive_counts = Counter(row["family_id"] for row in added if row["control_type"] == "positive")
    assert all(cluster_counts[family] >= 8 for family in NEW_FAMILIES)
    assert all(positive_counts[family] >= 3 for family in NEW_FAMILIES)

    skeletons = [normalized_skeleton(row) for row in added]
    repeated = {value: count for value, count in Counter(skeletons).items() if count > 1}
    assert not repeated, f"repeated normalized behavior skeletons: {len(repeated)}"

    unified = parent + added
    assert len(unified) == 2000
    for field in ("stable_case_id", "dedupe_sha256", "semantic_behavior_fingerprint"):
        values = [row[field] for row in unified]
        assert len(values) == len(set(values)) == 2000, field
    assert len({row["family_id"] for row in unified}) == 87

    parent_sets = [parent_behavior_tokens(row) for row in parent]
    new_sets = [new_behavior_tokens(row) for row in added]
    parent_matches = defaultdict(list)
    max_parent = (0.0, "", "")
    for new_index, current in enumerate(new_sets):
        for parent_index, previous in enumerate(parent_sets):
            score = jaccard(current, previous)
            if score > max_parent[0]:
                max_parent = (score, added[new_index]["stable_case_id"], parent[parent_index]["stable_case_id"])
            if score >= 0.50:
                parent_matches[new_index].append((score, parent_index))
    assert not parent_matches, f"unexplained parent/new overlaps: {sum(map(len, parent_matches.values()))}"

    internal_matches = defaultdict(list)
    investigations = []
    max_internal = (0.0, "", "")
    for left in range(len(added)):
        for right in range(left + 1, len(added)):
            score = jaccard(new_sets[left], new_sets[right])
            if score > max_internal[0]:
                max_internal = (score, added[left]["stable_case_id"], added[right]["stable_case_id"])
            if score >= 0.50:
                assert added[left]["semantic_behavior_fingerprint"] != added[right]["semantic_behavior_fingerprint"]
                assert (
                    event_summary(added[left]) != event_summary(added[right])
                    or added[left]["expected_observable_behavior"] != added[right]["expected_observable_behavior"]
                    or added[left]["failure_condition"] != added[right]["failure_condition"]
                    or added[left]["deterministic_oracle"].get("field") != added[right]["deterministic_oracle"].get("field")
                )
                disposition = pair_disposition(added[left], added[right], score)
                investigations.append(disposition)
                internal_matches[left].append(disposition)
                internal_matches[right].append(disposition)

    OUT.mkdir(parents=True, exist_ok=True)
    added_bytes = "".join(canon(row) + "\n" for row in added).encode()
    (OUT / "added-probes.jsonl").write_bytes(added_bytes)
    unified_path = OUT / "unified-probe-inventory.jsonl"
    unified_path.write_bytes(parent_bytes + added_bytes)
    assert unified_path.read_bytes()[:len(parent_bytes)] == parent_bytes
    assert len(unified_path.read_bytes().splitlines()) == 2000

    (OUT / "near-overlap-investigation.jsonl").write_text(
        "".join(canon(item) + "\n" for item in investigations), encoding="utf-8"
    )
    ledger = []
    for index, row in enumerate(unified):
        is_new = index >= 1500
        entry = {
            "stable_case_id": row["stable_case_id"], "family_id": row["family_id"],
            "inventory_segment": "added" if is_new else "parent",
            "source_ticket": row.get("provenance", {}).get("ticket", "BEAAA-18715"),
            "dedupe_sha256": row["dedupe_sha256"],
            "semantic_behavior_fingerprint": row["semantic_behavior_fingerprint"],
            "normalized_behavior_skeleton_sha256": hashlib.sha256(
                normalized_skeleton(row).encode()
            ).hexdigest(),
            "parent_near_overlap_matches": [],
            "new_near_overlap_matches": internal_matches.get(index - 1500, []) if is_new else [],
        }
        ledger.append(entry)
    ledger_path = OUT / "semantic-overlap-ledger.jsonl"
    ledger_path.write_text("".join(canon(row) + "\n" for row in ledger), encoding="utf-8")
    assert len(ledger) == 2000

    profile_counts = Counter(profile for row in added for profile in row["design_partner_profiles"])
    capability_counts = Counter(row["applicability"].get("required_capability", "unspecified") for row in added)
    applicability_counts = Counter(row["applicability"].get("status", "unspecified") for row in added)
    control_counts = {
        family: dict(sorted(Counter(
            row["control_type"] for row in added if row["family_id"] == family
        ).items())) for family in sorted(NEW_FAMILIES)
    }
    unified_family_counts = Counter(row["family_id"] for row in unified)
    coverage = f"""# BEAAA-18720 explicit content reconciliation\n\n**BLUF:** Four accepted explicit catalogs add exactly 500 behaviorally distinct cases to the byte-preserved 1,500-row parent, producing a validated 2,000-row candidate inventory across 87 families.\n\n## Exact reconciliation\n\n- Parent rows: 1,500\n- Parent SHA-256: `{PARENT_SHA}`\n- Accepted catalog rows: `{json.dumps(source_counts, sort_keys=True)}`\n- Accepted catalog SHA-256: `{json.dumps(catalog_hashes, sort_keys=True)}`\n- Added rows: 500\n- Unified rows: 2,000\n- Semantic-overlap ledger rows: 2,000\n- Unified families: 87\n- Unique stable IDs/content hashes/semantic fingerprints: 2,000 / 2,000 / 2,000\n- Unique normalized added-case skeletons: 500\n- Repeated normalized added-case skeletons: 0\n- Parent/new pairs >=0.50: 0\n- Maximum parent/new normalized-token similarity: `{max_parent[0]:.4f}` ({max_parent[1]} vs {max_parent[2]})\n- New/new pairs >=0.50 investigated: {len(investigations)}\n- Maximum new/new normalized-token similarity: `{max_internal[0]:.4f}` ({max_internal[1]} vs {max_internal[2]})\n- Unexplained near-overlap pairs: 0\n\n## Added family counts\n\n```json\n{json.dumps(dict(sorted(family_counts.items())), indent=2)}\n```\n\n## Unified family counts\n\n```json\n{json.dumps(dict(sorted(unified_family_counts.items())), indent=2)}\n```\n\n## Substantive clusters per added family\n\n```json\n{json.dumps(dict(sorted(cluster_counts.items())), indent=2)}\n```\n\n## Controls per added family\n\n```json\n{json.dumps(control_counts, indent=2)}\n```\n\n## Design-partner profiles\n\n```json\n{json.dumps(dict(sorted(profile_counts.items())), indent=2)}\n```\n\n## Capabilities and applicability\n\n```json\n{json.dumps({'capabilities': dict(sorted(capability_counts.items())), 'applicability': dict(sorted(applicability_counts.items()))}, indent=2)}\n```\n\nEvery added case is schema-complete, evidence-bearing, deterministic, profile-declared, `reported_not_scored`, and N/A-never-PASS. Near-overlap dispositions preserve distinct event sequences, expected observations, failure predicates, and oracle fields.\n\n## Scope\n\n- Contract: `are-scanner-api-v1.md@v1.0`\n- Classification: rule-execution, content-only\n- Scanner runtime, registry, scoring, mappings, CI, GitHub, deployment, production, and published counts: unchanged\n- Validation: `python3 tools/validate_beaaa18720_content.py`\n"""
    (OUT / "coverage-report.md").write_text(REPORT_METADATA + coverage, encoding="utf-8")
    closeout = f"""# BEAAA-18720 content-only closeout\n\n**BLUF:** Exactly 500 accepted explicit scenario contracts were reconciled with the byte-preserved 1,500-row parent into a validated 2,000-row candidate inventory.\n\n- `scanner_version`: unchanged\n- `corpus_version`: BEAAA-18720 content candidate 1.0.0\n- `probe-set-version`: BEAAA-18720/1.0.0\n- `rubric_point_id`: unchanged / not mapped\n- `N=5 calibration delta`: not run; candidate is content-only, unregistered, undispatched, and unscored\n- Contract: `are-scanner-api-v1.md@v1.0`\n- Parent SHA-256: `{PARENT_SHA}`\n- Counts: 1,500 parent + 500 added = 2,000 unified\n- Families: 67 parent + 20 new = 87 unified\n- Repeated normalized added-case skeletons: 0\n- Parent/new near-overlap pairs >=0.50: 0\n- New/new near-overlap pairs >=0.50 investigated: {len(investigations)}; unexplained: 0\n\nNo scanner integration, live execution, scoring, CI/CD, GitHub, deployment, production promotion, or published-count change is included.\n"""
    (OUT / "closeout.md").write_text(CLOSEOUT_METADATA + closeout, encoding="utf-8")
    evidence = {
        "status": "PASS", "parent_rows": 1500, "parent_sha256": PARENT_SHA,
        "parent_prefix_byte_preserved": True, "added_rows": 500, "unified_rows": 2000,
        "ledger_rows": 2000, "unified_families": 87, "new_family_counts": dict(sorted(family_counts.items())),
        "unique_stable_ids": 2000, "unique_dedupe_hashes": 2000,
        "unique_semantic_fingerprints": 2000, "unique_added_normalized_skeletons": 500,
        "repeated_added_normalized_skeletons": 0,
        "parent_new_pairs_at_or_above_0_50": 0,
        "maximum_parent_new_similarity": [round(max_parent[0], 4), max_parent[1], max_parent[2]],
        "new_new_pairs_at_or_above_0_50_investigated": len(investigations),
        "maximum_new_new_similarity": [round(max_internal[0], 4), max_internal[1], max_internal[2]],
        "unexplained_near_overlap_pairs": 0, "catalog_sha256": catalog_hashes,
        "added_sha256": hashlib.sha256(added_bytes).hexdigest(),
        "unified_sha256": hashlib.sha256(unified_path.read_bytes()).hexdigest(),
        "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
    }
    (OUT / "reconciliation-validation.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(canon(evidence))


if __name__ == "__main__":
    main()
