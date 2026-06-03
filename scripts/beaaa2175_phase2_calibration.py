"""BEAAA-2175 PR-B Phase 2 — full-dict byte-equivalence calibration vs.
master ``85d8be1`` (post-PR-A v2) with exhaustive ``intended_deltas``
accounting per CTO note #2 on BEAAA-2175 comment 63e50dc9.

The full-dict metric's value is precisely that an unintended delta is
detectable. ``intended_deltas`` is the contract that makes the metric
tight: if the listed-intended set differs from the observed-actual set
in either direction, the calibration HALTS and re-escalates.

Two calibration surfaces:

1. **CLI surface byte-equivalence vs. master.** PR-B does NOT modify
   ``harness/cli_runner.py`` or ``harness/contract_envelope.py``;
   the CLI projection must remain byte-identical vs. master. This is
   the PR-A v2 invariance argument carrying forward; any drift on this
   surface is unintended and halts. SHA-256 of
   ``json.dumps(run_scan(...), sort_keys=True)`` computed via subprocess
   against master ``85d8be1`` (sibling worktree) and PR-B
   (commit ``70bc043`` or later); per agent, per replicate; spreads
   computed.

2. **Rescan envelope surface vs. master.** This is where the **intended
   deltas** live. PR-B replaces 10 ``_phase1_pending`` placeholders with
   real scoring values; this is intentional, audited as a rule-change.
   The §2.2 envelope persisted to scan_log on master ``85d8be1`` carries
   the placeholder shape; on PR-B it carries the real-values shape. We
   diff the two envelopes key-by-key and assert:
   * Every diffed key is in ``INTENDED_DELTAS`` (no unintended delta).
   * Every key in ``INTENDED_DELTAS`` is actually in the diff (no missing
     intended delta — CTO's "if you list 12 and 11 differ, halt").

Output: ``tools/calibration-deltas/2026-06-03-beaaa2175-prb-full-dict-vs-master.json``.
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

PRB_REPO = Path("/tmp/are-scanner-prb")
MASTER_REPO = Path("/tmp/are-scanner-master")
MASTER_COMMIT = "85d8be1"
PRB_BRANCH = "beaaa-2175-pr-b"
GOLDEN_AGENTS = ("B1", "G1", "G2", "M1", "M2", "M3")
N_REPLICATES = 5
SEED = 42
FIXED_SCORED_AT = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc).isoformat()
OUTPUT_PATH = (
    PRB_REPO / "tools" / "calibration-deltas"
    / "2026-06-03-beaaa2175-prb-full-dict-vs-master.json"
)


#: BEAAA-2175 CTO note #2 — exhaustive list of envelope fields that are
#: EXPECTED to differ between the master baseline (post-PR-A v2 envelope
#: with `_phase1_pending` placeholders + master scanner_version) and the
#: PR-B candidate (envelope with real scoring values + bumped
#: scanner_version). Every other field MUST be byte-stable across the
#: two builds, OR be a key that is structurally per-scan-variable
#: (`run_timestamp`, the scan_id embedded in `forensic_bundle_uri`).
#:
#: Per-scan variable keys are listed in PER_SCAN_VOLATILE so the metric
#: knows to exclude them from the equivalence check (they would differ
#: even between two PR-B runs against each other; the test is per-build
#: drift, not per-run drift).
INTENDED_DELTAS: frozenset[str] = frozenset({
    # 10 placeholder-fill fields per ADR-0017 addendum-1 §2.2.
    "are_total",
    "band",
    "confidence_below_adjudication_threshold",
    "evidence_refs",
    "model_versions",
    "partial_completion",
    "purpose",
    "rubric_version",
    "seed",
    "subscores",
    # Scanner version bump per CTO note #1 + ticket pin.
    "scanner_version",
})

#: BEAAA-2175 CTO note #2 — keys that are per-scan-variable (timestamps,
#: scan_id-derived URIs). These are excluded from the equivalence check
#: because they differ across any two scans, even from the same build.
#: snapshot_hash IS recomputed by content over the envelope, so it moves
#: when envelope content moves; this is INTENDED per CTO note #2.
PER_SCAN_VOLATILE: frozenset[str] = frozenset({
    "run_timestamp",
    "forensic_bundle_uri",   # carries scan_id in the URI path
    "snapshot_hash",         # recomputed over envelope content; intended
    "prior_scan_refs",       # chain projection; differs based on prior /scans rows
    "scan_id",               # only in the rescan response wrapper, not the §2.2 envelope
    "status",                # only in the rescan response wrapper
    "purpose_aware_sla",     # SLA metric snapshot; non-zero only after first /rescans
})

#: Scanner version bump is its own delta.
SCANNER_VERSION_DELTA: dict[str, str] = {
    "master_baseline": "0.3.8",
    "prb_candidate": "0.3.10",
    "rationale": (
        "Master `85d8be1` carries SCANNER_VERSION=\"0.3.8\" per "
        "`git show 85d8be1:scanners/__init__.py`. The BEAAA-2175 ticket "
        "Pins section assumed master was at 0.3.9 (based on the WIP "
        "working-copy state from a separate agent's BEAAA-1721 sync "
        "WIP — not committed). PR-B bumps from the actual master value "
        "0.3.8 to 0.3.10, leaving 0.3.9 reserved for BEAAA-1721 if it "
        "lands separately. Per CTO note #1 on BEAAA-2175 comment "
        "63e50dc9: 'target 0.3.10 is correct'."
    ),
}


def _materialise_input_dir(
    agent: str, fixtures_root: Path, dst: Path
) -> Path:
    agent_dir = dst / agent
    agent_dir.mkdir(parents=True)
    (agent_dir / "eval_results.json").write_text(
        (fixtures_root / agent / "eval_outputs.json").read_text()
    )
    applicability = fixtures_root / agent / "applicability.json"
    if applicability.exists():
        shutil.copy(applicability, agent_dir / "applicability.json")
    return agent_dir


def _cli_run(repo: Path, agent: str, input_dir: Path) -> dict:
    """Invoke ``cli_runner.run_scan`` inside ``repo`` via subprocess. Returns
    the full contract output dict.
    """
    script = f"""
import json, sys
from datetime import datetime
sys.path.insert(0, {str(repo)!r})
from harness.cli_runner import run_scan
out = run_scan(
    agent_id={agent!r},
    input_dir=__import__('pathlib').Path({str(input_dir)!r}),
    seed={SEED},
    corpus_version=None,
    rubric_path=__import__('pathlib').Path({str(repo / 'rubric' / 'rubric.yaml')!r}),
    scored_at=datetime.fromisoformat({FIXED_SCORED_AT!r}),
)
print(json.dumps(out, sort_keys=True))
"""
    r = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def _digest(out: dict) -> str:
    return hashlib.sha256(
        json.dumps(out, sort_keys=True).encode("utf-8")
    ).hexdigest()


#: CLI-surface fields that legitimately differ between master and PR-B.
#: Strictly limited to ``scanner_version`` — the constant bump from 0.3.8
#: → 0.3.10. PR-B does not modify ``harness/cli_runner.py`` or
#: ``harness/contract_envelope.py``; every other CLI-output field MUST be
#: byte-stable.
CLI_INTENDED_DELTAS: frozenset[str] = frozenset({"scanner_version"})


def _normalize_cli_for_compare(out: dict) -> dict:
    """Strip the CLI intended-delta fields so two builds with different
    scanner_version pins still produce byte-equal digests for the rest of
    the output. Detects unintended drift on every other field.
    """
    return {k: v for k, v in out.items() if k not in CLI_INTENDED_DELTAS}


def calibrate_cli_surface() -> dict:
    """Per-agent SHA-256 of CLI full-dict output (minus CLI_INTENDED_DELTAS),
    master baseline vs. PR-B candidate; N=5 reproducibility on PR-B.
    PR-B does not modify cli_runner or contract_envelope, so the
    normalized digests MUST be byte-equal between master and PR-B; any
    unintended drift on any other field halts.
    """
    fixtures = PRB_REPO / "tests" / "fixtures" / "golden_agents"
    per_agent: dict = {}
    all_pass = True
    for agent in GOLDEN_AGENTS:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            master_in = _materialise_input_dir(
                agent, fixtures, scratch / "master_in"
            )
            master_out = _cli_run(MASTER_REPO, agent, master_in)
            master_digest_full = _digest(master_out)
            master_digest_norm = _digest(_normalize_cli_for_compare(master_out))

            prb_full_digests = []
            prb_norm_digests = []
            for i in range(N_REPLICATES):
                prb_in = _materialise_input_dir(
                    agent, fixtures, scratch / f"prb_in_{i}"
                )
                prb_out = _cli_run(PRB_REPO, agent, prb_in)
                prb_full_digests.append(_digest(prb_out))
                prb_norm_digests.append(
                    _digest(_normalize_cli_for_compare(prb_out))
                )

        replicates_equal = (
            len(set(prb_full_digests)) == 1
            and len(set(prb_norm_digests)) == 1
        )
        master_eq_prb_norm = master_digest_norm == prb_norm_digests[0]
        agent_pass = replicates_equal and master_eq_prb_norm
        all_pass = all_pass and agent_pass
        per_agent[agent] = {
            "master_digest_full": master_digest_full,
            "master_digest_normalized": master_digest_norm,
            "prb_digests_full": prb_full_digests,
            "prb_digests_normalized": prb_norm_digests,
            "replicates_identical": replicates_equal,
            "master_eq_prb_normalized": master_eq_prb_norm,
            "cli_intended_deltas_normalized_out": sorted(CLI_INTENDED_DELTAS),
            "pass": agent_pass,
        }
    return {"per_agent": per_agent, "all_pass": all_pass}


def _build_rescan_envelope(repo: Path, agent: str = "calib-agent") -> dict:
    """Fire a /scans + /rescans against the wired production runtime in
    ``repo`` via subprocess and return the **persisted** §2.2 envelope (the
    score_json before the handler stamps the 3 additive keys). The
    persisted envelope is the canonical surface for the intended_deltas
    comparison — it carries the 14 §2.2 fields, nothing else.
    """
    script = f"""
import json, os, sys, tempfile
sys.path.insert(0, {str(repo)!r})
os.environ['ARE_PRODUCTION_SCAN_WIRING'] = '1'
os.environ.pop('ARE_RESCANS_MEASURED', None)
_TMP = tempfile.mkdtemp(prefix='calib-')
os.environ['ARE_SCAN_LOG_PATH'] = _TMP + '/scan_log.sqlite3'

from api.scan_handlers import _kickoff_live_probe_scan, rescan_handler
from api.scan_runtime_factory import make_default_production_scan_runtime, build_v0_corpus_tarball, v0_corpus_snapshot_hash
from scanners import SCANNER_VERSION, DEFAULT_SEED

runtime = make_default_production_scan_runtime()
corpus_hash = v0_corpus_snapshot_hash(build_v0_corpus_tarball())

scan_body = {{
    'tenant_id': 'calib-tenant',
    'agent_endpoint': {{'host': 'calib.local', 'transport': 'rest'}},
    'credential_ref': 'calib.cred.v1.key', 'credential_secret': 's',
    'corpus_snapshot_hash': corpus_hash,
    'scanner_version': SCANNER_VERSION, 'corpus_version': '0.1.0',
    'seed': DEFAULT_SEED, 'scan_mode': 'live_probe', 'k_replicates': 5,
}}
_kickoff_live_probe_scan(
    scan_body, tenant_header='calib-tenant', runtime=runtime,
    scan_id_factory=lambda: '00000000-0000-0000-0000-000000000001',
    agent_id={agent!r},
)
rescan_body = dict(scan_body)
rescan_body['agent_id'] = {agent!r}
rescan_body['scanner_invocation_context'] = {{'purpose': 'pre_bind', 'requested_pillars': ['GOVERNANCE','ROBUSTNESS','BIAS_FAIRNESS','OPS']}}
rescan_handler(
    rescan_body, tenant_header='calib-tenant', runtime=runtime,
    scan_id_factory=lambda: '00000000-0000-0000-0000-000000000002',
)
# Recover the persisted envelope (score_json) — invariant target.
sid = list(runtime._kickoff_keys.keys())[-1]
row = runtime.current_kickoff_row(sid)
print(json.dumps(row.get('score_json') or {{}}, sort_keys=True))
"""
    r = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def calibrate_envelope_surface() -> dict:
    """Compare the persisted §2.2 envelope between master and PR-B builds.
    Assert the observed diff set equals the intended-deltas set
    (no unintended delta, no missing intended delta).
    """
    master_env = _build_rescan_envelope(MASTER_REPO)
    prb_env = _build_rescan_envelope(PRB_REPO)

    all_keys = set(master_env.keys()) | set(prb_env.keys())
    observed_diffs: set[str] = set()
    field_view: dict[str, dict] = {}
    for k in sorted(all_keys):
        master_v = master_env.get(k)
        prb_v = prb_env.get(k)
        if master_v == prb_v:
            continue
        if k in PER_SCAN_VOLATILE:
            field_view[k] = {
                "status": "per_scan_volatile",
                "master_present": k in master_env,
                "prb_present": k in prb_env,
            }
            continue
        observed_diffs.add(k)
        field_view[k] = {
            "status": "differs",
            "master_value_preview": _preview(master_v),
            "prb_value_preview": _preview(prb_v),
        }

    unintended = observed_diffs - INTENDED_DELTAS
    missing_intended = INTENDED_DELTAS - observed_diffs
    pass_ = (not unintended) and (not missing_intended)

    return {
        "master_envelope_keys": sorted(master_env.keys()),
        "prb_envelope_keys": sorted(prb_env.keys()),
        "intended_deltas": sorted(INTENDED_DELTAS),
        "per_scan_volatile_excluded": sorted(PER_SCAN_VOLATILE),
        "observed_diffs": sorted(observed_diffs),
        "unintended_diffs": sorted(unintended),
        "missing_intended_diffs": sorted(missing_intended),
        "field_view": field_view,
        "pass": pass_,
        "halt_reason": (
            None if pass_
            else (
                f"unintended diffs: {sorted(unintended)}; "
                f"missing intended diffs: {sorted(missing_intended)}"
            )
        ),
    }


def _preview(value) -> str:
    """One-line repr-ish preview of a value, capped at 80 chars."""
    if value is None:
        return "null"
    s = json.dumps(value, sort_keys=True, separators=(",", ":")) \
        if not isinstance(value, str) else value
    return s[:80] + ("..." if len(s) > 80 else "")


def main() -> int:
    report = {
        "ticket": "BEAAA-2175",
        "phase": "PR-B full-dict byte-equivalence vs. master with intended_deltas",
        "master_commit": MASTER_COMMIT,
        "prb_branch": PRB_BRANCH,
        "fixed_scored_at": FIXED_SCORED_AT,
        "seed": SEED,
        "n_replicates": N_REPLICATES,
        "scanner_version_delta": SCANNER_VERSION_DELTA,
        "cli_surface": {},
        "envelope_surface": {},
        "summary": {},
    }

    print("[cli surface] master vs. PR-B head-to-head, N=5 reproducibility per agent...")
    cli = calibrate_cli_surface()
    report["cli_surface"] = cli
    print(f"[cli surface] all_pass={cli['all_pass']}")

    print("[envelope surface] master vs. PR-B persisted envelope, intended_deltas accounted...")
    env = calibrate_envelope_surface()
    report["envelope_surface"] = env
    print(f"[envelope surface] pass={env['pass']}")
    if env["observed_diffs"]:
        print(f"        observed_diffs: {env['observed_diffs']}")
    if env["unintended_diffs"]:
        print(f"        UNINTENDED diffs (halt): {env['unintended_diffs']}")
    if env["missing_intended_diffs"]:
        print(f"        MISSING intended diffs (halt): {env['missing_intended_diffs']}")

    all_pass = cli["all_pass"] and env["pass"]
    report["summary"] = {
        "cli_surface_pass": cli["all_pass"],
        "envelope_surface_pass": env["pass"],
        "all_pass": all_pass,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nReport: {OUTPUT_PATH.relative_to(PRB_REPO)}")
    print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
