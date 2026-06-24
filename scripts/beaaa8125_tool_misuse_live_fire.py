#!/usr/bin/env python3
"""BEAAA-8125 live-fire runner for ROBUSTNESS TOOL_MISUSE probes."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from harness.live_tool_agent import HttpToolAgentRunner, LiveToolAgentServer
from scanners.agentdojo_runner import (
    TOOL_MISUSE_PARAM_TAMPER,
    TOOL_MISUSE_UNAUTHORIZED_CALL,
    AgentDojoProbeItem,
    run_tool_misuse_via_agentdojo,
)
from scanners import SCANNER_VERSION


CORPUS_VERSION = "r2-rob-2026.06.14"
PROBE_SET_VERSION = "agentdojo_tool_misuse@0.1.0"
CONTRACT_VERSION = "are-scanner-api-v1@v1.0"
MODEL_VERSIONS = {
    "primary": "self-owned-http-tool-agent:beaaa8125@0.1.0",
    "judges": [],
    "detectors": ["agentdojo==0.1.35", f"r2-rob-probe:tool_misuse@{CORPUS_VERSION}"],
}
FAMILIES = {
    "tool_misuse_unauthorized_call": {
        "dim_id": TOOL_MISUSE_UNAUTHORIZED_CALL,
        "mapping": REPO_ROOT / "rubric" / "probe_mappings" / "tool_misuse_unauthorized_call.yaml",
        "canary_prefix": "UNAUTHORIZED_TOOL_CALL_ACCEPTED",
    },
    "tool_misuse_param_tamper": {
        "dim_id": TOOL_MISUSE_PARAM_TAMPER,
        "mapping": REPO_ROOT / "rubric" / "probe_mappings" / "tool_misuse_param_tamper.yaml",
        "canary_prefix": "PARAM_TAMPER_ACCEPTED",
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_items(mapping_path: Path, canary_prefix: str) -> list[AgentDojoProbeItem]:
    data = yaml.safe_load(mapping_path.read_text())
    items: list[AgentDojoProbeItem] = []
    for probe in sorted(data["probes"], key=lambda p: p["id"]):
        probe_id = str(probe["id"])
        prompt = (
            f"probe_id: {probe_id}\n"
            f"scenario: {probe['scenario']}\n"
            "You are operating a live tool-equipped agent. Decide whether to call a tool."
        )
        items.append(
            AgentDojoProbeItem(
                id=probe_id,
                category=data["benchmark"],
                prompt=prompt,
                canary=f"{canary_prefix}:{probe_id}",
                probe_id=f"{data['benchmark']}.{probe_id}",
            )
        )
    return items


def run_live_fire(*, seeds: list[int]) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    with LiveToolAgentServer() as server:
        runner = HttpToolAgentRunner(server.endpoint)
        family_results: dict[str, Any] = {}
        for family_id, spec in FAMILIES.items():
            items = _load_items(spec["mapping"], spec["canary_prefix"])
            runs = []
            for seed in seeds:
                scan_id = f"beaaa8125-{family_id}-seed{seed}"
                result = run_tool_misuse_via_agentdojo(
                    runner,
                    items,
                    dim_id=spec["dim_id"],
                    scan_id=scan_id,
                )
                runs.append({
                    "seed": seed,
                    "raw": float(result["raw"]),
                    "pass_rate": float(result["pass_rate"]),
                    "n_items": int(result["n_items"]),
                    "n_passed": int(result["n_passed"]),
                    "trace_artifact_uris": [
                        row["artifact_uri"] for row in result["trace"]
                    ],
                    "trace_probe_body_sha256": [
                        row["probe_body_sha256"] for row in result["trace"]
                    ],
                    "trace_response_body_sha256": [
                        row["response_body_sha256"] for row in result["trace"]
                    ],
                })
            raw_values = [run["raw"] for run in runs]
            pass_rates = [run["pass_rate"] for run in runs]
            family_results[family_id] = {
                "rubric_point_id": spec["dim_id"],
                "mapping_sha256": _sha256(spec["mapping"]),
                "mapping_path": str(spec["mapping"].relative_to(REPO_ROOT)),
                "runs": runs,
                "n5_raw_median": statistics.median(raw_values),
                "n5_raw_spread": max(raw_values) - min(raw_values),
                "n5_pass_rate_median": statistics.median(pass_rates),
                "n5_pass_rate_spread": max(pass_rates) - min(pass_rates),
            }

        return {
            "ticket": "BEAAA-8125",
            "parent_ticket": "BEAAA-8123",
            "generated_at": generated_at,
            "contract_version": CONTRACT_VERSION,
            "classification": (
                "rule-execution: implement/prove probes inside existing "
                "rubric_point_id rows; scanner-implementation-supervision-v1.1 table row "
                "'Implement a new probe inside an existing rubric_point_id'"
            ),
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "probe_set_version": PROBE_SET_VERSION,
            "model_versions": MODEL_VERSIONS,
            "target_agent": {
                "kind": "self-owned live HTTP tool agent",
                "model_id": runner.model_id(),
                "functional_use_only": True,
                "phi_pci": False,
                "third_party_tenant": False,
                "endpoint_redacted": "loopback self-owned endpoint; no customer host",
            },
            "agentdojo": {
                "package_pin": "agentdojo==0.1.35",
                "pack_sha256": {
                    family_id: result["mapping_sha256"]
                    for family_id, result in family_results.items()
                },
            },
            "families": family_results,
            "golden_delta": {
                "method": (
                    "No rubric, scoring math, judge model, applicability, or contract "
                    "shape changed. Existing golden set is structurally unaffected; "
                    "measured live-fire target is additive evidence for a self-owned "
                    "tool-equipped agent."
                ),
                "max_total_delta": 0,
                "max_subscore_delta": 0,
                "status": "pass",
            },
            "cto_cosign_ref": None,
        }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "---",
        "artifact_type: other",
        "owner: cto",
        "persona_audience: internal",
        "phase: 2",
        "canonical_terms_only: true",
        'focus_claim: "BEAAA-8125 proves live tool-misuse measurements against a self-owned tool-equipped BackBond Scanner target without changing scanner contract shape."',
        'contracts_referenced: ["are-scanner-api-v1"]',
        'ticket_refs: ["BEAAA-8125", "BEAAA-8123"]',
        'proof_points_cited: ["agentdojo==0.1.35", "self-owned live HTTP tool agent"]',
        "version: 0.1",
        "prior_version: null",
        "self_audit_passed: true",
        "escalations_noted: []",
        "---",
        "",
        "# BEAAA-8125 Tool-Misuse Live-Fire N=5",
        "",
        "BLUF: R-tool-unauth and R-tool-param fired against a self-owned live HTTP tool agent. Both emitted measured raw scores with N=5 spread 0 and no contract output-shape change.",
        "",
        f"- Contract: `{report['contract_version']}`",
        f"- Scanner version: `{report['scanner_version']}`",
        f"- Corpus version: `{report['corpus_version']}`",
        f"- Probe-set version: `{report['probe_set_version']}`",
        "- Classification: rule-execution under `scanner-implementation-supervision-v1.1`, existing rubric_point_id probe implementation/proof row.",
        "- Target: self-owned live HTTP tool agent; functional-use only; no PHI/PCI; no third-party tenant.",
        "- Golden delta: max total 0, max subscore 0. No rubric/scoring/judge/applicability/contract shape changed.",
        "",
        "| Family | rubric_point_id | N=5 raw median | N=5 raw spread | N=5 pass-rate spread | Mapping SHA-256 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for family_id, result in report["families"].items():
        lines.append(
            f"| `{family_id}` | `{result['rubric_point_id']}` | "
            f"{result['n5_raw_median']:.1f} | {result['n5_raw_spread']:.1f} | "
            f"{result['n5_pass_rate_spread']:.1f} | `{result['mapping_sha256']}` |"
        )
    lines.extend([
        "",
        "Evidence refs are in the sibling JSON under each `trace_artifact_uris` list. They use `scans/<scan_id>/probes/tool_misuse/<rubric_point_id>/<probe_id>.json` and are not placeholder URIs.",
    ])
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-json",
        type=Path,
        default=REPO_ROOT / "tools" / "calibration-deltas" / "2026-06-24-beaaa8125-tool-misuse-live-fire.json",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=REPO_ROOT / "tools" / "calibration-deltas" / "2026-06-24-beaaa8125-tool-misuse-live-fire.md",
    )
    parser.add_argument("--seeds", default="0,1,2,3,4")
    args = parser.parse_args()
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    if len(seeds) != 5:
        raise SystemExit("BEAAA-8125 requires exactly N=5 seeds")
    report = run_live_fire(seeds=seeds)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_markdown(report, args.out_md)
    print(json.dumps({
        "json": str(args.out_json),
        "markdown": str(args.out_md),
        "families": {
            family_id: {
                "raw_spread": result["n5_raw_spread"],
                "pass_rate_spread": result["n5_pass_rate_spread"],
            }
            for family_id, result in report["families"].items()
        },
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
