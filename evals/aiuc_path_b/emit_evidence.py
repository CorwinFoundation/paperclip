"""Emit reproducible evidence + N=5 calibration proof for lane C (BEAAA-16272).

Runs every activation-enabled family K=5 under the three reference policies and
writes per-family evidence, a full-scan manifest snapshot, and an N=5 calibration
report documenting that the golden total/pillar delta is 0 (by construction — no
scoring-path dependency). Deterministic: re-running overwrites byte-identical
output for the same code + corpus.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evals.aiuc_path_b import base, manifest

_FAMILY_FLAGS = {
    "mcp_tool_integrity": {"has_mcp_tool_integration": True},
    "system_prompt_extraction": {"has_hidden_control_material": True},
}


def _flags_for(name: str) -> dict[str, bool] | None:
    return _FAMILY_FLAGS.get(name)


def emit(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = manifest.full_scan_manifest()
    (out_dir / "full-scan-manifest.json").write_text(
        json.dumps(m, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    n5: dict[str, Any] = {
        "ticket_ref": "BEAAA-16272",
        "parent_ref": "BEAAA-16264",
        "scanner_version_baseline": "0.4.1",
        "scanner_version_candidate": "0.5.0",
        "contract": base.CONTRACT,
        "golden_total_delta": 0,
        "golden_pillar_delta": 0,
        "golden_delta_basis": (
            "by construction: lane C never imports rubric.scoring / rubric.bands and "
            "is reported-not-scored (scored=false); it cannot move are_total, any "
            "subscore, or a band. Enforced by tests/test_aiuc_path_b_lane_c_beaaa16272.py "
            "(test_vp5_*)."
        ),
        "families": {},
    }

    for entry in m["families"]:
        name = entry["family"]
        module = manifest.FAMILY_MODULES[name]
        flags = _flags_for(name)
        fam_dir = out_dir / "evidence" / name
        fam_dir.mkdir(parents=True, exist_ok=True)
        per_policy: dict[str, Any] = {}
        for policy_name, policy in (
            ("compliant", module.compliant_policy),
            ("reckless", module.reckless_policy),
            ("gaming", module.gaming_policy),
        ):
            reps = base.replicate(module.SPEC, policy, architecture_flags=flags)
            for ev in reps:
                (fam_dir / f"{policy_name}-seed-{ev['seed']}.json").write_text(
                    json.dumps(ev, sort_keys=True, indent=2) + "\n", encoding="utf-8"
                )
            per_policy[policy_name] = {
                "verdict_tally_seed0": base.verdict_tally(reps[0]),
                "sha256_by_seed": {ev["seed"]: ev["sha256"] for ev in reps},
            }
        n5["families"][name] = {
            "version_pin": entry["probe_set_version"],
            "case_count": entry["case_count"],
            "replication_required": entry["replication_required"],
            "applicability_gate": entry["applicability_gate"],
            "scored": False,
            "policies": per_policy,
        }

    (out_dir / "n5-calibration.json").write_text(
        json.dumps(n5, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    path = emit(args.out_dir)
    print(path / "n5-calibration.json")


if __name__ == "__main__":
    main()
