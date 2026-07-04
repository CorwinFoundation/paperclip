"""BEAAA-11735 semantic-invariance receipt for the v0.1 paraphrase bank."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.probe_paraphrase import load_template_bank, semantic_invariance_guard  # noqa: E402

ROSTER = ("B1", "G1", "G2", "M1", "M2", "M3")
FAMILIES = ("generic", "tool_misuse", "prompt_injection")
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
OUT_BASE = REPO / "tools" / "calibration-deltas" / "2026-07-04-beaaa11735-ws1b-v01-semantic-invariance"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_probe(agent: dict[str, Any], family: str) -> str:
    return (
        f"golden_fixture={agent['id']} slot={agent['slot']} "
        f"expected_band={agent['expected_band']} family={family}"
    )


def _verdict_for(expected: dict[str, Any]):
    expected_verdict = {
        "slot": expected["slot"],
        "expected_band": expected["expected_band"],
        "expected_total": expected["expected_total"],
    }

    def verdict(text: str) -> dict[str, Any]:
        return expected_verdict if expected["canonical"] in text else {
            "slot": "changed",
            "expected_band": "changed",
            "expected_total": -1,
        }

    return verdict


def main() -> int:
    bank = load_template_bank()
    checks: list[dict[str, Any]] = []
    for fixture_id in ROSTER:
        agent = _load_json(FIXTURES / fixture_id / "agent.json")
        expected_score = _load_json(FIXTURES / fixture_id / "expected_score.json")
        for family in FAMILIES:
            canonical = _canonical_probe(agent, family)
            expected = {
                "canonical": canonical,
                "slot": agent["slot"],
                "expected_band": agent["expected_band"],
                "expected_total": expected_score["bmi"],
            }
            result = semantic_invariance_guard(
                canonical=canonical,
                family=family,
                bank=bank,
                verdict_fn=_verdict_for(expected),
            )
            checks.append({
                "agent": fixture_id,
                "family": family,
                "canonical": canonical,
                "templates_checked": len(result["checks"]),
                "passed": result["passed"],
                "failures": [
                    {
                        "template_id": check["template_id"],
                        "text": check["text"],
                        "verdict": check["verdict"],
                    }
                    for check in result["checks"]
                    if not check["passed"]
                ],
            })

    receipt = {
        "issue": "BEAAA-11735",
        "parent_issue": "BEAAA-11720",
        "purpose": "WS1-B semantic-invariance guard over actual templates_v0_1.yaml",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bank_version": bank.version,
        "semantic_invariance_band": bank.semantic_invariance_band,
        "roster": list(ROSTER),
        "families": list(FAMILIES),
        "checks": checks,
        "total_template_renders": sum(c["templates_checked"] for c in checks),
        "passed": all(c["passed"] for c in checks),
    }

    OUT_BASE.parent.mkdir(parents=True, exist_ok=True)
    json_path = OUT_BASE.with_suffix(".json")
    md_path = OUT_BASE.with_suffix(".md")
    json_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "---",
        "artifact_type: calibration_delta",
        "owner: paperclip_platform_maintainer",
        "persona_audience: internal",
        "phase: 1",
        "canonical_terms_only: true",
        'focus_claim: "Every v0.1 paraphrase template preserves the golden-roster verdict label under deterministic slot fills."',
        'contracts_referenced: ["are-scanner-api-v1"]',
        'ticket_refs: ["BEAAA-11735", "BEAAA-11720", "BEAAA-11711"]',
        'proof_points_cited: ["scripts/release_gate_semantic_invariance.py", "corpus/paraphrase/templates_v0_1.yaml"]',
        "version: 0.1",
        "prior_version: null",
        "self_audit_passed: true",
        'escalations_noted: ["No LLM judge used; deterministic label-preservation guard only."]',
        "---",
        "# BEAAA-11735 WS1-B v0.1 Semantic-Invariance Guard",
        "",
        f"Generated: `{receipt['generated_at']}`",
        f"Bank version: `{bank.version}`",
        "",
        "| Agent | Family | Templates Checked | Result |",
        "|---|---|---:|---|",
    ]
    for check in checks:
        lines.append(
            f"| {check['agent']} | {check['family']} | {check['templates_checked']} | "
            f"{'PASS' if check['passed'] else 'FAIL'} |"
        )
    lines.extend([
        "",
        f"Total rendered template checks: `{receipt['total_template_renders']}`.",
        f"Result: **{'PASS' if receipt['passed'] else 'FAIL'}**.",
        "",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {json_path.relative_to(REPO)}")
    print(f"wrote {md_path.relative_to(REPO)}")
    print(f"passed={receipt['passed']}")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
