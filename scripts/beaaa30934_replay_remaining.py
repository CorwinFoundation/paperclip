"""Resume only the 40 unresolved BEAAA-30934 Vapi rows."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

from api.full_scan_executor import FullScanJobStore, dispatch_registry_attempt, registry_cases_from_rows
from api.full_scan_worker import load_default_full_scan_registry_rows
from harness.customer_runner import customer_agent_http_runner_factory
from scripts import scheduled_scan as scheduled
from tools.registered_case_executor import ScanBudget, build_registered_case_workers, default_judge_runner

SOURCE = Path("/var/lib/backbond/scans/2026-09-08/vapi/evidence/d7a46f24-48b5-4174-88e4-3ab5b0305a18")
PRIOR = Path("/tmp/beaaa-30934-replay-20260909T1205Z/evidence/be25ada6-4390-4fca-b47e-feabb8e7e0e5")
OUT = Path("/var/lib/backbond/scans/beaaa-30934-replay-remaining")


def main() -> int:
    source_ids = {p.stem for p in SOURCE.glob("*/*.json") if str(json.loads(p.read_text()).get("verdict", "")).lower() == "unresolved"}
    prior_ids = {p.stem for p in PRIOR.glob("*/*.json")}
    rows = [r for r in load_default_full_scan_registry_rows() if str(r.get("case_id")) in source_ids - prior_ids]
    if len(source_ids) != 69 or len(prior_ids) != 29 or len(rows) != 40:
        raise RuntimeError(f"replay roster mismatch: source={len(source_ids)} prior={len(prior_ids)} selected={len(rows)}")

    config = scheduled.ScheduleConfig.from_environment()
    secrets = scheduled.map_secret_environment()
    limiter = scheduled.SharedRateLimiter(30)
    request = SimpleNamespace(agent_endpoint=config.agent_endpoint, agent_id=secrets["VAPI_ASSISTANT_ID"], tenant_id=config.tenant_id)
    subject = lambda endpoint, secret: scheduled.RateLimitedRunner(customer_agent_http_runner_factory(endpoint, secret, secrets["VAPI_ASSISTANT_ID"]), limiter)
    judge = lambda: scheduled.RateLimitedRunner(default_judge_runner(), limiter)
    workers = build_registered_case_workers(request, secrets["VAPI_API_KEY"], budget=ScanBudget(max_target_calls=100, max_judge_calls=100, max_estimated_judge_cost_usd=10, max_wall_clock_seconds=7200), evidence_root=OUT / "evidence", subject_factory=subject, judge_factory=judge)
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
