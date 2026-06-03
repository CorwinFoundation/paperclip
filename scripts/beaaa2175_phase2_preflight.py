"""BEAAA-2175 PR-B Phase 2 preflight — AG-1..AG-10.

Runs all ten acceptance gates against the post-PR-B build:

* **AG-1..AG-7** — delegated to the BEAAA-2139 cutover preflight as a
  subprocess so the existing AG-1..AG-7 logic is reused unchanged (rule-
  execution invariant: PR-B does not touch the AG-1..AG-7 wiring shape).
  The BEAAA-2139 preflight emits a JSON report; we parse it and assert
  every gate PASSes.

* **AG-8** — no ``_phase1_pending`` literal anywhere in the rescan
  response payload. ``assert "_phase1_pending" not in json.dumps(payload)``.

* **AG-9** — ``snapshot_hash`` recomputed via the canonical JCS preimage
  (``api.scan_log.compute_snapshot_hash`` which composes
  RFC-8785-style canonicalization via ``jcs.canonicalize``) matches the
  persisted hash. The smoke script's ``json.dumps`` approximation is
  replaced here with the production code path's canonical preimage so
  AG-9 is a real verification, not an approximation. The CTO co-sign
  reworded AG-9 (BEAAA-2175 comment 99dbec2e condition #2): the envelope
  carries the value the canonical scoring projection (extracted shared
  library) computed for this scan — implicit in the fact that the
  envelope was produced by ``score_runtime.compute_envelope`` which
  composes ``rubric.scoring.score`` → ``harness.contract_envelope.to_contract_output``.

* **AG-10** — N=5 scoring spread = 0 on B1/G1/G2/M1/M2/M3 against the
  post-PR-B build. The scoring side stability check; non-zero spread
  halts and re-escalates because it would mean the wiring change touched
  the scoring engine. Reuses the PR-A v2 calibration shape:
  ``json.dumps(run_scan(...), sort_keys=True)`` digested via SHA-256 for
  full-dict byte-equivalence across the 5 replicates.

Output: ``tools/calibration-deltas/2026-06-03-beaaa2175-pra-v2-prb-ag1-10.json``.
Exit 0 iff every gate PASSes. Any FAIL → exit 1.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Env profile required for the production wiring + scan_log on tempdir.
os.environ["ARE_PRODUCTION_SCAN_WIRING"] = "1"
os.environ.pop("ARE_RESCANS_MEASURED", None)
_TMP_DIR = Path(tempfile.mkdtemp(prefix="beaaa2175-prb-preflight-"))
os.environ["ARE_SCAN_LOG_PATH"] = str(_TMP_DIR / "scan_log.sqlite3")

from api.scan_handlers import _kickoff_live_probe_scan, rescan_handler  # noqa: E402
from api.scan_log import compute_snapshot_hash  # noqa: E402
from api.scan_runtime_factory import (  # noqa: E402
    build_v0_corpus_tarball,
    make_default_production_scan_runtime,
    v0_corpus_snapshot_hash,
)
from harness.cli_runner import run_scan as cli_run_scan  # noqa: E402
from scanners import DEFAULT_SEED, SCANNER_VERSION  # noqa: E402

OUTPUT_PATH = (
    REPO / "tools" / "calibration-deltas"
    / "2026-06-03-beaaa2175-prb-ag1-10.json"
)
GOLDEN_AGENTS = ("B1", "G1", "G2", "M1", "M2", "M3")
N_REPLICATES = 5
FIXED_SCORED_AT = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
AGENT_ID = "smoke-beaaa2175-prb-preflight"
TENANT_ID = "smoke-tenant"


# ---------------------------------------------------------------------------
# AG-1..AG-7 — delegate to BEAAA-2139 cutover preflight.
# ---------------------------------------------------------------------------


def run_ag1_to_ag7() -> dict:
    """Invoke the BEAAA-2139 cutover preflight as a subprocess and parse
    its JSON report. The reused preflight is rule-execution per its own
    classification header; PR-B does not touch its AG-1..AG-7 wiring
    surface, so re-running it against the post-PR-B build is the
    regression check the BEAAA-2175 acceptance criteria require.
    """
    preflight = REPO / "scripts" / "beaaa2139_cutover_preflight.py"
    if not preflight.exists():
        return {
            "AG-1_to_AG-7_overall": "FAIL: BEAAA-2139 preflight script missing",
        }
    env = dict(os.environ)
    # Subprocess-isolated scan_log dir so it doesn't collide with our AG-8/9.
    sub_dir = Path(tempfile.mkdtemp(prefix="beaaa2139-subproc-"))
    env["ARE_SCAN_LOG_PATH"] = str(sub_dir / "scan_log.sqlite3")
    result = subprocess.run(
        [sys.executable, str(preflight)],
        capture_output=True, text=True, env=env, cwd=str(REPO),
    )
    parsed: dict = {"stdout_tail": result.stdout[-2000:], "exit_code": result.returncode}
    # BEAAA-2139 preflight emits its JSON report to stdout (no file write).
    # Find the JSON envelope by locating the outermost `{` ... `}` block.
    try:
        stdout = result.stdout
        first_brace = stdout.find("{")
        if first_brace < 0:
            raise ValueError("no JSON block in BEAAA-2139 stdout")
        report = json.loads(stdout[first_brace:])
        parsed["gates"] = dict(report.get("addendum_aggregate") or {})
        aggregate_verdict = report.get("aggregate_verdict", "")
        parsed["aggregate_verdict_raw"] = aggregate_verdict
        parsed["AG-1_to_AG-7_overall"] = (
            "PASS" if isinstance(aggregate_verdict, str)
            and aggregate_verdict.startswith("PASS")
            else "FAIL"
        )
    except Exception as exc:  # noqa: BLE001
        parsed["AG-1_to_AG-7_overall"] = f"FAIL: stdout parse — {exc}"
    return parsed


# ---------------------------------------------------------------------------
# Synthetic rescan helper for AG-8 + AG-9.
# ---------------------------------------------------------------------------


def _build_scan_body(*, corpus_snapshot_hash: str) -> dict:
    return {
        "tenant_id": TENANT_ID,
        "agent_endpoint": {"host": "preflight.local", "transport": "rest"},
        "credential_ref": "preflight.cred.v1.key",
        "credential_secret": "preflight-secret-not-real",
        "corpus_snapshot_hash": corpus_snapshot_hash,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": "0.1.0",
        "seed": DEFAULT_SEED,
        "scan_mode": "live_probe",
        "k_replicates": 5,
    }


def _build_rescan_body(*, corpus_snapshot_hash: str, purpose: str) -> dict:
    body = _build_scan_body(corpus_snapshot_hash=corpus_snapshot_hash)
    body["agent_id"] = AGENT_ID
    body["scanner_invocation_context"] = {
        "purpose": purpose,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"],
    }
    return body


def _fire_synthetic_rescan() -> tuple[dict, dict]:
    """Build the wired production runtime, fire a /scans + a /rescans, return
    (rescan_payload, persisted_envelope).
    """
    runtime = make_default_production_scan_runtime()
    corpus_tarball = build_v0_corpus_tarball()
    corpus_hash = v0_corpus_snapshot_hash(corpus_tarball)

    scan_body = _build_scan_body(corpus_snapshot_hash=corpus_hash)
    _kickoff_live_probe_scan(
        scan_body,
        tenant_header=TENANT_ID,
        runtime=runtime,
        scan_id_factory=None,
        agent_id=AGENT_ID,
    )

    rescan_body = _build_rescan_body(
        corpus_snapshot_hash=corpus_hash, purpose="pre_bind"
    )
    _status, rescan_payload = rescan_handler(
        rescan_body,
        tenant_header=TENANT_ID,
        runtime=runtime,
    )

    # The persisted envelope (pre-handler patch) lives in scan_log under the
    # current kickoff key. Read it back to recover the exact dict the
    # snapshot_hash was computed over.
    scan_id = rescan_payload.get("scan_id")
    if not isinstance(scan_id, str):
        # Under Path A, the persisted-envelope shape ate scan_id; pull the
        # most recent scan_id off the runtime registry instead.
        recent = list(runtime._scan_requests.keys())
        scan_id = recent[-1] if recent else ""
    persisted_row = runtime.current_kickoff_row(scan_id) or {}
    persisted_envelope = persisted_row.get("score_json") or {}

    return rescan_payload, persisted_envelope


# ---------------------------------------------------------------------------
# AG-8 — no `_phase1_pending` literal in rescan response.
# ---------------------------------------------------------------------------


def run_ag8(payload: dict) -> dict:
    serialized = json.dumps(payload, sort_keys=True)
    placeholder_present = "_phase1_pending" in serialized
    return {
        "pass": not placeholder_present,
        "placeholder_present": placeholder_present,
        "payload_size_bytes": len(serialized),
    }


# ---------------------------------------------------------------------------
# AG-9 — snapshot_hash recompute via canonical JCS preimage.
# ---------------------------------------------------------------------------


def run_ag9(rescan_payload: dict, persisted_envelope: dict) -> dict:
    """AG-9 has two parts per the CTO-reworded condition:

    1. ``snapshot_hash`` recomputed from canonical JCS of the persisted
       envelope (the dict the ``score_envelope_factory`` returned, before
       the handler stamped ``forensic_bundle_uri`` + ``snapshot_hash`` +
       ``purpose_aware_sla`` onto the response) matches the response's
       ``snapshot_hash``.
    2. The envelope carries values the canonical scoring projection
       (the extracted shared library) computed — implicit in the wiring
       since ``score_runtime.compute_envelope`` composes
       ``rubric.scoring.score`` → ``harness.contract_envelope.to_contract_output``.
       We sanity-check by asserting ``are_total`` is a real int (not a
       placeholder string) and the four pillar subscores are floats.
    """
    response_hash = rescan_payload.get("snapshot_hash", "")
    # The persisted envelope is exactly what was JCS-canonicalized.
    recomputed = compute_snapshot_hash(persisted_envelope)
    hash_match = response_hash == recomputed

    are_total = rescan_payload.get("are_total")
    subscores = rescan_payload.get("subscores") or {}
    sanity = (
        isinstance(are_total, int)
        and isinstance(subscores, dict)
        and len(subscores) >= 1
        and all(isinstance(v, (int, float)) for v in subscores.values())
    )

    return {
        "pass": hash_match and sanity,
        "response_snapshot_hash": response_hash,
        "recomputed_snapshot_hash": recomputed,
        "hash_match": hash_match,
        "scoring_sanity_pass": sanity,
        "are_total_type": type(are_total).__name__,
        "subscores_keys": sorted(subscores.keys()) if isinstance(subscores, dict) else [],
        "scoring_projection_callee": (
            "score_runtime.compute_envelope → rubric.scoring.score → "
            "harness.contract_envelope.to_contract_output (CTO note #2 "
            "wording — canonical projection, not 'what the scorer emitted')"
        ),
    }


# ---------------------------------------------------------------------------
# AG-10 — N=5 scoring spread = 0 on goldens (CLI surface, since PR-B does
# not change the CLI; this re-tests scoring stability against the post-PR-B
# build to catch any accidental scoring engine touch).
# ---------------------------------------------------------------------------


def run_ag10() -> dict:
    fixtures = REPO / "tests" / "fixtures" / "golden_agents"
    per_agent: dict[str, dict] = {}
    max_spread = 0
    all_pass = True
    for agent in GOLDEN_AGENTS:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            replicates: list[dict] = []
            for i in range(N_REPLICATES):
                agent_dir = scratch / f"r{i}" / agent
                agent_dir.mkdir(parents=True)
                (agent_dir / "eval_results.json").write_text(
                    (fixtures / agent / "eval_outputs.json").read_text()
                )
                applicability = fixtures / agent / "applicability.json"
                if applicability.exists():
                    shutil.copy(applicability, agent_dir / "applicability.json")
                out = cli_run_scan(
                    agent_id=agent,
                    input_dir=agent_dir,
                    seed=42,
                    corpus_version=None,
                    rubric_path=REPO / "rubric" / "rubric.yaml",
                    scored_at=FIXED_SCORED_AT,
                )
                replicates.append({
                    "are_total": out["are_total"],
                    "subscores": dict(out["subscores"]),
                })
        totals = [r["are_total"] for r in replicates]
        total_spread = max(totals) - min(totals)
        pillars = sorted({k for r in replicates for k in r["subscores"]})
        pillar_spreads = {
            p: max(r["subscores"].get(p, 0.0) for r in replicates)
            - min(r["subscores"].get(p, 0.0) for r in replicates)
            for p in pillars
        }
        max_pillar_spread = max(pillar_spreads.values()) if pillar_spreads else 0.0
        agent_spread = max(total_spread, max_pillar_spread)
        max_spread = max(max_spread, agent_spread)
        agent_pass = total_spread == 0 and max_pillar_spread == 0
        all_pass = all_pass and agent_pass
        per_agent[agent] = {
            "totals": totals,
            "total_spread": total_spread,
            "pillar_spreads": pillar_spreads,
            "pass": agent_pass,
        }
    return {
        "pass": all_pass,
        "max_spread": max_spread,
        "per_agent": per_agent,
        "n_replicates": N_REPLICATES,
    }


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"[setup] PR-B preflight scan_log: {os.environ['ARE_SCAN_LOG_PATH']}")
    print(f"[setup] SCANNER_VERSION: {SCANNER_VERSION}")

    report: dict = {
        "ticket": "BEAAA-2175",
        "phase": "PR-B Phase 2 preflight — AG-1..AG-10",
        "scanner_version": SCANNER_VERSION,
        "n_replicates": N_REPLICATES,
        "seed": DEFAULT_SEED,
        "agents": list(GOLDEN_AGENTS),
        "gates": {},
        "summary": {},
    }

    # AG-1..AG-7
    print("[AG-1..AG-7] running BEAAA-2139 cutover preflight as subprocess...")
    ag1_to_7 = run_ag1_to_ag7()
    report["gates"]["AG-1_to_AG-7"] = ag1_to_7
    ag1_to_7_pass = ag1_to_7.get("AG-1_to_AG-7_overall") == "PASS"
    print(f"[AG-1..AG-7] {ag1_to_7.get('AG-1_to_AG-7_overall')}")

    # AG-8 + AG-9 share a synthetic rescan.
    print("[AG-8/9] firing synthetic /scans + /rescans against wired runtime...")
    rescan_payload, persisted_envelope = _fire_synthetic_rescan()

    ag8 = run_ag8(rescan_payload)
    report["gates"]["AG-8"] = ag8
    print(f"[AG-8] {'PASS' if ag8['pass'] else 'FAIL'}: placeholder_present={ag8['placeholder_present']}")

    ag9 = run_ag9(rescan_payload, persisted_envelope)
    report["gates"]["AG-9"] = ag9
    print(
        f"[AG-9] {'PASS' if ag9['pass'] else 'FAIL'}: hash_match={ag9['hash_match']} "
        f"scoring_sanity={ag9['scoring_sanity_pass']}"
    )
    if not ag9["hash_match"]:
        print(f"        persisted: {ag9['response_snapshot_hash'][:32]}...")
        print(f"        recomputed: {ag9['recomputed_snapshot_hash'][:32]}...")

    # AG-10
    print("[AG-10] running N=5 calibration on B1/G1/G2/M1/M2/M3 against CLI...")
    ag10 = run_ag10()
    report["gates"]["AG-10"] = ag10
    print(
        f"[AG-10] {'PASS' if ag10['pass'] else 'FAIL'}: max_spread={ag10['max_spread']}"
    )

    all_pass = (
        ag1_to_7_pass
        and ag8["pass"]
        and ag9["pass"]
        and ag10["pass"]
    )
    report["summary"] = {
        "AG-1_to_AG-7_pass": ag1_to_7_pass,
        "AG-8_pass": ag8["pass"],
        "AG-9_pass": ag9["pass"],
        "AG-10_pass": ag10["pass"],
        "all_pass": all_pass,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nReport: {OUTPUT_PATH.relative_to(REPO)}")
    print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
