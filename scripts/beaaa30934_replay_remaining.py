"""Resume the unresolved BEAAA-30934 Vapi rows.

Roster derivation is durable.  The pending set is the unresolved case ids of
the 2026-09-08 source scan minus the case ids this replay has already written
a real verdict for under its own evidence root, so the script is resumable and
re-runnable without depending on anything outside the scan manifest root.

The first cut subtracted a prior replay directory under ``/tmp`` and asserted
the exact triple ``source=69 prior=29 selected=40``.  That directory is gone,
so the assertion could only raise and the run produced no rows (BEAAA-30934).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# The systemd unit runs `python scripts/...` from /opt/backbond/src with no
# PYTHONPATH, so `api`, `harness`, and `tools` were not importable there.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Written authorization for this replay: the Founder on BEAAA-30934,
# 2026-10-06T18:47Z ("You have full authorization to do any test you want on
# Vapi"). Used only when the unit's environment does not already name one, so
# the BEAAA-35702 gate still checks a ref and an expiry, and this lapses on its own.
FOUNDER_VAPI_AUTH_REF = "BEAAA-30934#comment-b3c74059-1533-4749-a4d3-c1b1ec1017dc"
FOUNDER_VAPI_AUTH_EXPIRES = "2026-10-13T18:47:34Z"

SOURCE = Path(
    os.environ.get(
        "BEAAA30934_SOURCE_EVIDENCE",
        "/var/lib/backbond/scans/2026-09-08/vapi/evidence/d7a46f24-48b5-4174-88e4-3ab5b0305a18",
    )
)
OUT = Path(
    os.environ.get(
        "BEAAA30934_REPLAY_ROOT",
        "/var/lib/backbond/scans/beaaa-30934-replay-remaining",
    )
)
MAX_TARGET_CALLS = 100


def _verdicts(evidence_root: Path) -> dict[str, str]:
    """Map case_id -> lowercased recorded verdict under an evidence root."""
    verdicts: dict[str, str] = {}
    for path in sorted(evidence_root.glob("*/*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            verdicts[path.stem] = str(record.get("verdict", "")).lower()
    return verdicts


def unresolved_case_ids(evidence_root: Path) -> set[str]:
    """Case ids the source scan left without a verdict."""
    return {case_id for case_id, verdict in _verdicts(evidence_root).items() if verdict == "unresolved"}


def resolved_case_ids(evidence_root: Path) -> set[str]:
    """Case ids this replay has already resolved, so a re-run skips them."""
    if not evidence_root.exists():
        return set()
    return {
        case_id
        for case_id, verdict in _verdicts(evidence_root).items()
        if verdict not in ("", "unresolved")
    }


def pending_case_ids(source_evidence: Path, replay_evidence: Path) -> set[str]:
    """The roster: still-unresolved source rows this replay has not resolved."""
    if not source_evidence.exists():
        raise FileNotFoundError(f"source evidence root is missing: {source_evidence}")
    return unresolved_case_ids(source_evidence) - resolved_case_ids(replay_evidence)


def select_rows(rows, case_ids: set[str]) -> list:
    """Registry rows for ``case_ids``, refusing a roster the budget cannot serve."""
    selected = [row for row in rows if str(row.get("case_id")) in case_ids]
    missing = sorted(case_ids - {str(row.get("case_id")) for row in selected})
    if missing:
        raise RuntimeError(f"{len(missing)} pending case ids have no registry row: {missing[:10]}")
    if len(selected) > MAX_TARGET_CALLS:
        raise RuntimeError(
            f"roster of {len(selected)} rows exceeds the {MAX_TARGET_CALLS}-call replay budget"
        )
    return selected


def main() -> int:
    from api.full_scan_executor import FullScanJobStore, dispatch_registry_attempt, registry_cases_from_rows
    from api.full_scan_worker import load_default_full_scan_registry_rows
    from harness.customer_runner import customer_agent_http_runner_factory
    from scripts import scheduled_scan as scheduled
    from tools.registered_case_executor import ScanBudget, build_registered_case_workers, default_judge_runner

    # This unit sends live traffic to the same non-production Vapi assistant as
    # the weekly `vapi` lane, so it gates on the same written authorization
    # (BEAAA-35702). It runs before the roster read and before
    # `map_secret_environment()` for the reason that gate's docstring gives:
    # the order is the control, and a later check would already have read the
    # credential. BEAAA-35868 found this path ungated while the sudoers
    # drop-in from install-units-only.sh hands an agent seat `systemctl start`
    # on it.
    os.environ.setdefault(scheduled.VAPI_AUTH_REF_ENV, FOUNDER_VAPI_AUTH_REF)
    os.environ.setdefault(scheduled.VAPI_AUTH_EXPIRES_ENV, FOUNDER_VAPI_AUTH_EXPIRES)
    scheduled.require_vapi_authorization("vapi")

    replay_evidence = OUT / "evidence"
    case_ids = pending_case_ids(SOURCE, replay_evidence)
    rows = select_rows(load_default_full_scan_registry_rows(), case_ids)
    print(json.dumps({"source": str(SOURCE), "pending": len(case_ids), "selected": len(rows)}))
    if not rows:
        print(json.dumps({"status": "nothing_to_replay"}))
        return 0

    config = scheduled.ScheduleConfig.from_environment()
    secrets = scheduled.map_secret_environment()
    limiter = scheduled.SharedRateLimiter(30)
    request = SimpleNamespace(agent_endpoint=config.agent_endpoint, agent_id=secrets["VAPI_ASSISTANT_ID"], tenant_id=config.tenant_id)
    subject = lambda endpoint, secret: scheduled.RateLimitedRunner(customer_agent_http_runner_factory(endpoint, secret, secrets["VAPI_ASSISTANT_ID"]), limiter)
    judge = lambda: scheduled.RateLimitedRunner(default_judge_runner(), limiter)
    workers = build_registered_case_workers(request, secrets["VAPI_API_KEY"], budget=ScanBudget(max_target_calls=MAX_TARGET_CALLS, max_judge_calls=MAX_TARGET_CALLS, max_estimated_judge_cost_usd=10, max_wall_clock_seconds=7200), evidence_root=replay_evidence, subject_factory=subject, judge_factory=judge)
    store = FullScanJobStore()
    job = store.submit(agent_id=secrets["VAPI_ASSISTANT_ID"], scanner_version=config.scanner_version, corpus_version=config.corpus_version, model_versions=next(iter(workers.values())).manifest_metadata(), seed=config.seed, registry_cases=registry_cases_from_rows(rows), scan_id="beaaa-30934-replay-remaining", wall_clock_budget_seconds=7200, rate_limit_per_minute=30)
    lease = store.lease(job.job_id, ttl_seconds=7200)
    dispatch_registry_attempt(store, job.job_id, attempt_id=str(lease["attempt_id"]), registry_rows=rows, workers=workers)
    final = dict(store.finalize(job.job_id, attempt_id=str(lease["attempt_id"])).final_manifest or {})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(final, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"job_id": job.job_id, "selected": len(rows), "finished_at": time.time()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
