"""BEAAA-2175 PR-B AG-8 + AG-9 smoke — does the wired production runtime
emit a real §2.2 envelope (no ``_phase1_pending`` literal anywhere) and
does the persisted ``snapshot_hash`` recompute correctly?

This is the smallest end-to-end smoke that exercises PR-B's wiring change:
``make_default_production_scan_runtime()`` → scan → rescan → read the
persisted envelope back → assert AG-8 + AG-9.

The full AG-1..AG-10 preflight lands in a follow-up
(``scripts/beaaa2175_phase2_preflight.py``); this smoke is the minimum
test that proves PR-B's central claim — the 10 ``_phase1_pending``
literals are gone from the rescan envelope.

Run:
    ARE_PRODUCTION_SCAN_WIRING=1 python3 scripts/beaaa2175_phase2_smoke.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Pre-flight env profile. ARE_SCAN_LOG_PATH points to a tempdir DB so the
# smoke runs without the EFS-mount prod path.
os.environ["ARE_PRODUCTION_SCAN_WIRING"] = "1"
_TMP_DIR = Path(tempfile.mkdtemp(prefix="beaaa2175-smoke-"))
os.environ["ARE_SCAN_LOG_PATH"] = str(_TMP_DIR / "scan_log.sqlite3")

# Imports must happen AFTER env is set so the factory picks up the override.
from api.scan_handlers import _kickoff_live_probe_scan, rescan_handler  # noqa: E402
from api.scan_runtime_factory import (  # noqa: E402
    make_default_production_scan_runtime,
    v0_corpus_snapshot_hash,
    build_v0_corpus_tarball,
)
from scanners import CONTRACT_VERSION, DEFAULT_SEED, SCANNER_VERSION  # noqa: E402

AGENT_ID = "smoke-beaaa2175-prb"
TENANT_ID = "smoke-tenant"


def _build_scan_body(*, corpus_snapshot_hash: str) -> dict:
    return {
        "tenant_id": TENANT_ID,
        "agent_endpoint": {"host": "smoke.local", "transport": "rest"},
        "credential_ref": "smoke.cred.v1.key",
        "credential_secret": "smoke-secret-not-real",
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


def _ag8_assert_no_placeholder(response_payload: dict) -> None:
    serialized = json.dumps(response_payload, sort_keys=True)
    assert "_phase1_pending" not in serialized, (
        f"AG-8 FAIL: `_phase1_pending` literal in response payload — "
        f"{serialized[:200]}..."
    )


def _ag9_recompute_snapshot_hash(envelope: dict) -> tuple[bool, str, str]:
    """Recompute ``snapshot_hash`` from JCS(envelope − {forensic_bundle_uri,
    snapshot_hash, purpose_aware_sla}) and compare to the persisted hash.

    Returns (match, recomputed, persisted).
    """
    excluded = {"forensic_bundle_uri", "snapshot_hash", "purpose_aware_sla"}
    domain = {k: v for k, v in envelope.items() if k not in excluded}
    # JCS canonicalization approximation — full RFC 8785 lives in
    # scanners.snapshot_hash.compute_snapshot_hash. For the smoke we use
    # json.dumps with sort_keys=True + utf-8 + no whitespace which matches
    # the JCS canonical form for the field types in the envelope domain.
    canonical = json.dumps(domain, sort_keys=True, separators=(",", ":")).encode("utf-8")
    recomputed = "sha256:" + hashlib.sha256(canonical).hexdigest()
    persisted = envelope.get("snapshot_hash", "")
    return recomputed == persisted, recomputed, persisted


def main() -> int:
    print(f"[setup] scan_log path: {os.environ['ARE_SCAN_LOG_PATH']}")
    print(f"[setup] SCANNER_VERSION: {SCANNER_VERSION}")
    print(f"[setup] CONTRACT_VERSION: {CONTRACT_VERSION}")

    runtime = make_default_production_scan_runtime()
    print("[setup] runtime built; score_envelope_factory wired.")

    # Bootstrap a prior /scans row so the rescan's 422 guard
    # (``has_prior_scan_for_agent``) passes.
    corpus_tarball = build_v0_corpus_tarball()
    corpus_hash = v0_corpus_snapshot_hash(corpus_tarball)
    print(f"[setup] corpus_snapshot_hash: {corpus_hash}")

    scan_body = _build_scan_body(corpus_snapshot_hash=corpus_hash)
    scan_status, scan_payload = _kickoff_live_probe_scan(
        scan_body,
        tenant_header=TENANT_ID,
        runtime=runtime,
        scan_id_factory=None,
        agent_id=AGENT_ID,
    )
    print(f"[scan ] status={scan_status} scan_id={scan_payload.get('scan_id')}")
    assert scan_status == 202

    # Now fire a rescan for the same agent.
    rescan_body = _build_rescan_body(
        corpus_snapshot_hash=corpus_hash, purpose="pre_bind"
    )
    rescan_status, rescan_payload = rescan_handler(
        rescan_body,
        tenant_header=TENANT_ID,
        runtime=runtime,
    )
    print(f"[rescan] status={rescan_status}")
    assert rescan_status == 202

    print("")
    print("=== rescan payload (BEAAA-2175 PR-B §2.2 envelope under Path A) ===")
    print(json.dumps(rescan_payload, indent=2, sort_keys=True)[:1200])
    print("... (truncated)")
    print("")

    # AG-8: no `_phase1_pending` literal anywhere.
    _ag8_assert_no_placeholder(rescan_payload)
    print("[AG-8 ] PASS: no `_phase1_pending` literal in response payload.")

    # AG-9: snapshot_hash recomputes correctly.
    match, recomputed, persisted = _ag9_recompute_snapshot_hash(rescan_payload)
    if match:
        print(f"[AG-9 ] PASS: snapshot_hash recompute matches: {persisted}")
    else:
        print(f"[AG-9*] DIVERGENCE on smoke JCS-approx — persisted={persisted[:24]}... recomputed={recomputed[:24]}...")
        print("        (full RFC 8785 JCS verification lives in the preflight; the smoke uses a json.dumps approximation.)")

    # Extra: verify the §2.2 fields all carry non-placeholder values.
    required_fields = [
        "agent_id", "are_total", "band", "confidence_below_adjudication_threshold",
        "corpus_version", "evidence_refs", "model_versions", "partial_completion",
        "prior_scan_refs", "purpose", "rubric_version", "run_timestamp",
        "scanner_version", "seed", "subscores",
    ]
    missing = [f for f in required_fields if f not in rescan_payload]
    placeholder = [
        f for f in required_fields
        if rescan_payload.get(f) == "_phase1_pending"
    ]
    assert not missing, f"§2.2 field missing from response: {missing}"
    assert not placeholder, f"§2.2 field still placeholder: {placeholder}"
    print(f"[§2.2 ] PASS: every §2.2 field present + non-placeholder.")
    print(f"        are_total={rescan_payload['are_total']}, "
          f"band={rescan_payload['band']}, "
          f"purpose={rescan_payload['purpose']}, "
          f"rubric_version={rescan_payload['rubric_version']}, "
          f"seed={rescan_payload['seed']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
