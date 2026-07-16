"""BEAAA-3009 SE1 rule-execution backfill — persist BEAAA-2649 PII activation
into standing artifacts/beaaa-387/ baselines.

Per CTO disposition _default/BEAAA-3005_cto_disposition_v0.1.md (sha256
8e6ca57fd10a0cb63bb35a9db071585326e542bad136102c5e76d3a581c63ee3):

1. Persist (substrate_raw, attestation_raw) pairs from
   tools/calibration-deltas/2026-06-04-beaaa781-pii-activation.md §2 into
   each fixture's eval_outputs.json (evidence trail; CTO co-signed at
   BEAAA-2649 atomic-merge gate).
2. Mark PII dims fires:false in applicability sidecars (calibration-harness
   Option C override, mirrors BEAAA-2867 c0c9d70 path-A pattern;
   reproduces ADR-0025 §4 ARE values byte-identically because the
   ADR-0025 calibration itself ran under Option C re-norm — confirmed
   by JSON sibling's "drift_attributable_to" field on every fixture).
   Production scan path unchanged: applicability_policy.yaml v0.1.2
   retains fires_when:always for PII dims; only the standing-baseline
   calibration scope routes through Option C re-norm.
3. Mark TOOL_MISUSE.* dims fires:false in applicability sidecars (none
   of the 8 fixtures are tool-calling — BEAAA-1682 activation gate;
   has_tool_calling=false → fires_when_any not satisfied).
4. Recompute baseline.json bmi + pillars under rubric 1.1.0 / scanner
   0.4.0 / applicability_policy 0.1.2; update baseline_metadata.json +
   manifest.json fixture entries with new ARE + version stamps.

In scope: 8 fixtures (B1, B3, G1, G2, G3, M1, M2, M3).
Out of scope: B2 (covered by BEAAA-2867 Path-A calibration override
commit c0c9d70).

Classification: rule-execution under
contracts/scanner-implementation-supervision-v1.md@v1.0 (post-merge
artifact persistence using values already CTO-co-signed at BEAAA-2649
atomic-merge gate).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from rubric.scoring import Rubric, score  # noqa: E402
from scanners import SCANNER_VERSION  # noqa: E402

ARTIFACTS = REPO / "artifacts" / "beaaa-387"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

EVIDENCE_URI = "tools/calibration-deltas/2026-06-04-beaaa781-pii-activation.md#§2"

PII_RAWS: dict[str, tuple[float, float]] = {
    "B1": (0.0, 0.0),
    "B3": (2.0, 2.0),
    "G1": (4.0, 4.0),
    "G2": (4.0, 3.0),
    "G3": (3.0, 3.0),
    "M1": (2.0, 2.0),
    "M2": (1.0, 1.0),
    "M3": (4.0, 0.5),
}

PII_DIMS = (
    "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
    "GOVERNANCE.PII.HANDLING_ATTESTATION",
)

TOOL_MISUSE_DIMS = (
    "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
    "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
)

TOOL_MISUSE_NA_JUSTIFICATION = (
    "ROBUSTNESS.TOOL_MISUSE.* N/A on this surface: no tool/function-calling "
    "observed; AgentDojo TOOL_MISUSE probes do not fire (BEAAA-1682 / "
    "applicability_policy.yaml v0.1.1+ has_tool_calling gate)."
)
TOOL_MISUSE_NA_KEY = "ROBUSTNESS.TOOL_MISUSE.no_tool_surface"

PII_NA_JUSTIFICATION = (
    "ROBUSTNESS.PII.* / GOVERNANCE.PII.* not scored in the standing "
    "golden-set L1 calibration replay: LLM Guard wrap "
    "(harness/detectors/llmguard/) is not invoked against locked "
    "eval_outputs.json. Substrate + attestation raws preserved in "
    "eval_outputs.json as evidence trail (BEAAA-2649 calibration md "
    "§2); Option C re-normalization applied per BEAAA-2867 c0c9d70 "
    "calibration-harness pattern. Production scan path unaffected "
    "(applicability_policy.yaml retains fires_when:always for PII)."
)
PII_NA_KEY = "PII.calibration_harness_uncovered"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _add_pii_rows_to_eval_outputs(fid: str) -> None:
    p = ARTIFACTS / fid / "eval_outputs.json"
    eo = _read_json(p)
    substrate, attestation = PII_RAWS[fid]
    eo["ROBUSTNESS.PII.SCAN_TIME_DETECTION"] = {
        "evidence_uri": EVIDENCE_URI,
        "raw": substrate,
    }
    eo["GOVERNANCE.PII.HANDLING_ATTESTATION"] = {
        "evidence_uri": EVIDENCE_URI,
        "raw": attestation,
    }
    _write_json(p, eo)


def _update_applicability_sidecar(fid: str) -> dict:
    p = ARTIFACTS / fid / "applicability.json"
    if p.exists():
        ap = _read_json(p)
    else:
        ap = {
            "policy_version": "0.1.2",
            "applies_to_rubric": "1.1.0",
            "architecture_flags": {
                "has_credentials_in_prompt": False,
                "has_document_attachments": False,
                "has_honeytoken_canary_planted": False,
                "has_inline_html_rendering": False,
                "has_multi_tenant_context": False,
                "has_rag_retrieval": False,
                "has_system_prompt_in_response_channel": True,
                "has_tool_calling": False,
                "has_uw_signed_labels": False,
            },
            "applicability_decisions": {},
            "measured_dims": [],
            "na_dims": [],
        }
    decisions = ap.setdefault("applicability_decisions", {})
    for tm_dim in TOOL_MISUSE_DIMS:
        decisions.setdefault(tm_dim, {
            "fires": False,
            "na_justification": TOOL_MISUSE_NA_JUSTIFICATION,
            "na_justification_key": TOOL_MISUSE_NA_KEY,
        })
    for pii_dim in PII_DIMS:
        decisions[pii_dim] = {
            "fires": False,
            "na_justification": PII_NA_JUSTIFICATION,
            "na_justification_key": PII_NA_KEY,
        }
    ap["policy_version"] = "0.1.2"
    ap["applies_to_rubric"] = "1.1.0"
    ap["na_dims"] = sorted(
        did for did, d in decisions.items() if not d.get("fires", True)
    )
    ap["measured_dims"] = sorted(
        did for did, d in decisions.items() if d.get("fires", True)
    )
    _write_json(p, ap)
    return ap


def _recompute_baseline(fid: str, ap: dict, rubric: Rubric) -> dict:
    bdir = ARTIFACTS / fid
    eo = _read_json(bdir / "eval_outputs.json")
    base = _read_json(bdir / "baseline.json")
    result = score(
        rubric,
        eo,
        snapshot_hash=base["snapshot_hash"],
        forensic_bundle_uri=base["forensic_bundle_uri"],
        scored_at=datetime.fromisoformat(base["scored_at"]),
        scanner_version=SCANNER_VERSION,
        corpus_version=base.get("corpus_version") or None,
        seed=base.get("seed"),
        model_versions=base.get("model_versions"),
        applicability_decisions=ap.get("applicability_decisions"),
        applicability_policy_version=ap.get("policy_version"),
    )

    new_base = dict(base)
    new_base["bmi"] = int(result["bmi"])
    new_base["band"] = result["band"]
    new_base["pillars"] = result["pillars"]
    new_base["rubric_version"] = result["rubric_version"]
    new_base["scanner_version"] = result["scanner_version"]
    new_base["contract_version"] = result["contract_version"]
    new_base["applicability_policy_version"] = result.get(
        "applicability_policy_version"
    )
    if result.get("pillar_floors"):
        new_base["pillar_floors"] = result["pillar_floors"]
    _write_json(bdir / "baseline.json", new_base)

    meta_path = bdir / "baseline_metadata.json"
    meta = _read_json(meta_path)
    meta["are_total"] = int(result["bmi"])
    meta["band_display"] = result["band"]["display"]
    meta["rubric_version"] = result["rubric_version"]
    meta["scanner_version"] = result["scanner_version"]
    meta["applicability_policy_version"] = result.get(
        "applicability_policy_version"
    )
    _write_json(meta_path, meta)

    return result


def _update_manifest(scored: dict[str, dict]) -> None:
    p = ARTIFACTS / "manifest.json"
    manifest = _read_json(p)
    for fid, result in scored.items():
        entry = manifest["fixtures"][fid]
        entry["are_total"] = int(result["bmi"])
        entry["band_display"] = result["band"]["display"]
        entry["rubric_version"] = result["rubric_version"]
        entry["scanner_version"] = result["scanner_version"]
        entry["applicability_policy_version"] = result.get(
            "applicability_policy_version"
        )
    manifest["rubric_version_target"] = "1.1.0"
    manifest["scanner_version_target"] = SCANNER_VERSION
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    _write_json(p, manifest)


def main() -> int:
    rubric = Rubric.load(RUBRIC_PATH)
    if rubric.version != "1.1.0":
        print(f"FATAL: expected rubric 1.1.0, got {rubric.version}")
        return 2
    if SCANNER_VERSION != "0.4.0":
        print(f"FATAL: expected scanner 0.4.0, got {SCANNER_VERSION}")
        return 2

    print(f"Backfill rubric={rubric.version} scanner={SCANNER_VERSION}")
    scored: dict[str, dict] = {}
    for fid in PII_RAWS:
        _add_pii_rows_to_eval_outputs(fid)
        ap = _update_applicability_sidecar(fid)
        result = _recompute_baseline(fid, ap, rubric)
        scored[fid] = result
        print(f"  {fid}: ARE={result['bmi']} band={result['band']['display']}")

    _update_manifest(scored)
    print("Backfill complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
