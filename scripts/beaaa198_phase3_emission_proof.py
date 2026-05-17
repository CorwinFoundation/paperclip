"""BEAAA-198 Phase 3 emission proof — N/A scoring math end-to-end.

Demonstrates that the BEAAA-182 consumer-chat architecture flag set
(all 8 architecture flags False, mirroring scan_lawconnect_atticus.py
and scan_lawconnect_ailawyer.py) round-trips through rubric.scoring.score
under Option C and produces:

  - P3.2 / P3.3 emit raw=null + byte-stable na_justification +
    na_justification_key sourced verbatim from rubric/applicability_policy.yaml
  - P3 pillar score is re-normalized across P3.1 only (the lone firing dim)
  - response carries applicability_policy_version stamp
  - response.diagnostics contains a coverage_caveat naming option=C and
    listing the N/A dim ids + justifications

Used for BEAAA-198 Track 2 Phase 3 handback evidence. Reproducible with:

    PYTHONPATH=. python3 scripts/beaaa198_phase3_emission_proof.py

Exits non-zero on any acceptance failure.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import applicability  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
POLICY_PATH = REPO / "rubric" / "applicability_policy.yaml"

# Mirror scan_lawconnect_atticus.py ARCHITECTURE_FLAGS exactly. The same
# flag set is used by scan_lawconnect_ailawyer.py.
BEAAA_182_CONSUMER_CHAT_FLAGS = {
    "has_tool_calling": False,
    "has_rag_retrieval": False,
    "has_document_attachments": False,
    "has_inline_html_rendering": False,
    "has_system_prompt_in_response_channel": False,
    "has_credentials_in_prompt": False,
    "has_honeytoken_canary_planted": False,
    "has_multi_tenant_context": False,
}


def _eval_outputs_for_consumer_chat(rubric: Rubric) -> dict:
    """Synthetic measured raws for every dim that fires on a consumer-chat
    surface. Numbers are illustrative — the proof is about the emission
    shape, not the calibration value. (Real raws come from the bench
    runners; this script is a unit-level proof of the scoring path.)"""
    # Use a flat 3.0 across all firing dims so the math is obvious.
    return {
        did: {"raw": 3.0, "evidence_uri": f"https://demo.local/beaaa-182/{did}"}
        for did in rubric.active_dim_ids
        if did not in ("P3.2", "P3.3")
    }


def main() -> int:
    rubric = Rubric.load(RUBRIC_PATH)
    policy = applicability.load_policy(POLICY_PATH)
    decisions = applicability.decide(policy, BEAAA_182_CONSUMER_CHAT_FLAGS)
    decisions_dict = {
        did: {
            "fires": d.fires,
            "na_justification": d.na_justification,
            "na_justification_key": d.na_justification_key,
        }
        for did, d in decisions.items()
    }
    eo = _eval_outputs_for_consumer_chat(rubric)

    resp = score(
        rubric,
        eo,
        snapshot_hash="sha256:" + "b" * 64,
        forensic_bundle_uri="https://demo.local/beaaa-182/proof",
        scored_at=datetime(2026, 5, 17, 0, 0, 0, tzinfo=timezone.utc),
        applicability_decisions=decisions_dict,
        applicability_policy_version=policy.version,
    )

    failures: list[str] = []

    p3_dims = resp["pillars"]["P3"]["dimensions"]
    if p3_dims["P3.2"]["raw"] is not None:
        failures.append("P3.2 raw is not null on consumer-chat surface")
    if p3_dims["P3.3"]["raw"] is not None:
        failures.append("P3.3 raw is not null on consumer-chat surface")
    if p3_dims["P3.2"]["na_justification_key"] != "P3.2.no_external_content_channel":
        failures.append("P3.2 na_justification_key not policy-sourced")
    if p3_dims["P3.3"]["na_justification_key"] != "P3.3.no_secret_bearing_surface":
        failures.append("P3.3 na_justification_key not policy-sourced")

    # Re-norm check: P3 pillar = mean(P3.1)/4 × 25 = 3.0/4 × 25 = 18.75.
    if abs(resp["pillars"]["P3"]["score"] - 18.75) > 1e-6:
        failures.append(f"P3 re-norm produced {resp['pillars']['P3']['score']}, expected 18.75")

    if resp.get("applicability_policy_version") != policy.version:
        failures.append("applicability_policy_version not stamped on response")

    diags = resp.get("diagnostics", [])
    caveat = next((d for d in diags if d["code"] == "coverage_caveat"), None)
    if caveat is None:
        failures.append("coverage_caveat diagnostic missing")
    else:
        if caveat["detail"]["scoring_policy_option"] != "C":
            failures.append("scoring_policy_option != C in caveat detail")
        if sorted(caveat["detail"]["na_dims"]) != ["P3.2", "P3.3"]:
            failures.append("na_dims list mismatch in caveat detail")

    print(json.dumps({
        "bmi": resp["bmi"],
        "band": resp["band"]["display"],
        "p3_pillar_score": resp["pillars"]["P3"]["score"],
        "p3_dims": {
            did: {
                "raw": p3_dims[did]["raw"],
                "applicable": p3_dims[did].get("applicable", True),
                "na_justification_key": p3_dims[did].get("na_justification_key"),
            }
            for did in ("P3.1", "P3.2", "P3.3")
        },
        "applicability_policy_version": resp["applicability_policy_version"],
        "diagnostics_codes": [d["code"] for d in diags],
        "coverage_caveat_na_dims": caveat["detail"]["na_dims"] if caveat else None,
        "acceptance": "PASS" if not failures else "FAIL",
        "failures": failures,
    }, indent=2))

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
