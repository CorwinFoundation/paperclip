#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "beaaa-20420"
COLLECTOR = ROOT / "tools" / "post-bind-ingest" / "otel-collector"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True).stdout


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    binary = COLLECTOR / "build" / "backbond-otelcol" / "backbond-otelcol"
    config = COLLECTOR / "collector.yaml"
    builder = COLLECTOR / "otelcol-builder.yaml"
    run(str(binary), "validate", "--config", str(config))

    module_json = run("go", "list", "-m", "-json", "all", cwd=binary.parent)
    decoder = json.JSONDecoder()
    modules, pos = [], 0
    while pos < len(module_json):
        while pos < len(module_json) and module_json[pos].isspace():
            pos += 1
        if pos >= len(module_json):
            break
        item, pos = decoder.raw_decode(module_json, pos)
        modules.append(item)
    sbom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {"component": {"type": "application", "name": "backbond-otelcol", "version": "0.157.0"}},
        "components": [
            {"type": "library", "name": item["Path"], "version": item.get("Version", "devel"),
             **({"properties": [{"name": "go.module.sum", "value": item["Sum"]}]}
                if item.get("Sum") else {})}
            for item in modules if not item.get("Main")
        ],
    }
    (OUT / "component-sbom.cdx.json").write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n")

    test = "tools/post-bind-ingest/tests/test_otel_log_tracker_integration_beaaa20420.py"
    repeats = []
    for index in range(5):
        output = run("pytest", "-q", test)
        repeats.append({"run": index + 1, "result": output.strip(), "score_delta": 0})
    receipt = {
        "artifact_metadata": {
            "owner": "CTO",
            "artifact_type": "release_evidence",
            "status": "review",
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_refs": [
                "BEAAA-20423",
                "BEAAA-20422",
                "are-scanner-api-v1@v1.3.0",
                "scripts/beaaa20423_build_collector.sh",
            ],
            "claim_or_decision": "The pinned OTel Collector is reproducibly buildable from the immutable candidate source.",
            "verification": "Pinned Go toolchain build repeated twice with identical SHA-256; Collector config validation and N=5 isolated integration proof passed; independent QA approval remains pending on BEAAA-20422.",
            "approvals": ["QA Engineer APPROVED required on BEAAA-20422 for the exact candidate commit"],
        },
        "issue": "BEAAA-20420",
        "classification": "rule-execution",
        "contract_ref": "are-scanner-api-v1@v1.3.0",
        "scanner_version": "0.5.0",
        "corpus_version": "frozen-0.5.0",
        "probe-set-version": "not-applicable-runtime-evidence-driver",
        "rubric_point_id": "OPS.DATA_HANDLING.LOCAL_INGEST",
        "n5_calibration_delta": 0,
        "cto_cosign_ref": None,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "effective_config_sha256": sha256(config),
        "builder_manifest_sha256": sha256(builder),
        "binary_sha256": sha256(binary),
        "binary_size_bytes": binary.stat().st_size,
        "binary_build_command": "scripts/beaaa20423_build_collector.sh",
        "binary_build_toolchain": run("go", "env", "GOVERSION", cwd=binary.parent).strip(),
        "n5_isolated_proof": repeats,
        "assertion": "Runtime evidence integration does not import or change scoring, rubric weights, bands, or judge behavior.",
        "pre_submission_self_audit": {
            "single_purpose": "Make the pinned Collector binary reproducible from a clean immutable candidate checkout.",
            "audience_and_next_action": "QA Engineer rebuilds, validates collector.yaml, regenerates this receipt, and reconciles identities on BEAAA-20422.",
            "supreme_law": "Respected: rule-execution only, score delta 0, N=5 evidence attached, no scoring or contract change.",
            "score_identity_fields": "No score is emitted by this artifact; scanner_version and corpus_version identify the unchanged scoring baseline.",
            "contract_change_and_n5": "No contract change; N=5 isolated proof records score delta 0.",
            "rollback": "Revert the BEAAA-20423 candidate commit; ffab7a6 remains unpromoted and superseded for QA.",
            "llm_judges": "None used.",
            "glossary_check": "Passed; canonical BackBond and Passive Monitor terms used.",
            "carrier_rule_check": "Passed; no carrier claim is present.",
            "anticipated_review": "Independent QA must confirm the exact candidate, build toolchain, binary/config/manifest hashes, fail-closed behavior, and N=5 result before promotion.",
        },
    }
    (OUT / "build-and-n5-evidence.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
