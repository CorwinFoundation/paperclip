"""QA BEAAA-18614 — isolation-property proof (ruling D.4 / ruling E).

Property under proof
--------------------
With all 44 lane-C rows executing and returning real numeric results, report-only
rows contribute ZERO WEIGHT through the aggregation path.

Design notes
------------
1. ADAPTER-INDEPENDENT. The property belongs to the aggregation path, not to any
   one adapter, so this drives the real `dispatch_registry_attempt` + `finalize`
   with a STUB worker whose numeric output I control. That means it runs against
   current master today — where no lane-C adapter exists — and against any future
   candidate unchanged. It cannot be satisfied by a cooperative adapter.

2. IT MUST BE ABLE TO FAIL. The assertion this replaces
   (`json.dumps(X) == json.dumps(X)`) was rejected as inert coverage. So this file
   ends with a self-test that deliberately breaks the aggregation path and asserts
   the proof REPORTS FAILURE against the broken build. A proof that passes on both
   the real and the sabotaged build proves nothing, and this file says so out loud.

3. Adversarial numeric inputs. Not just 1.0/0.0 — also huge, negative and NaN
   values, because "contributes zero weight" must hold for any value an adapter
   could emit, not only the two the current adapter happens to emit.

Usage:  python3 qa_isolation_proof.py           (run from a repo worktree root)
Exit 0 = property proven AND proof demonstrated non-inert.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")

from api import full_scan_executor as fse
from api.full_scan_executor import (
    EvidenceAttemptRow,
    FullScanJobStore,
    WorkerAttemptResult,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from tools.active_test_registry import build_registry
from tools.framework_worker_registry import worker_id_for_registry_row

LANE_C = {
    "human_escalation_flagging",
    "customer_defined_risk_pack",
    "mcp_tool_integrity",
}

# Everything the published score / underwriting weight could ride on.
AGGREGATION_SURFACE = ("score_contribution", "disposition", "bind_eligibility")


def lane_c_rows():
    rows = [r for r in build_registry() if r["test_id"] in LANE_C]
    if len(rows) != 44:
        raise SystemExit(f"expected 44 lane-C rows, found {len(rows)}")
    return rows


class StubWorker:
    """Stands in for whatever adapter a candidate ships.

    Emits a caller-chosen numeric_result for every case, so the proof exercises
    the aggregation path rather than an adapter's good behaviour.
    """

    def __init__(self, numeric_fn, evidence_dir: Path):
        self.numeric_fn = numeric_fn
        self.evidence_dir = evidence_dir
        self.n = 0

    def __call__(self, registry_row, job):
        case_id = str(registry_row.get("case_id") or "")
        value = self.numeric_fn(self.n, case_id)
        self.n += 1
        ref = self.evidence_dir / f"{self.n}.json"
        ref.write_text(json.dumps({"case_id": case_id, "score": value}), encoding="utf-8")
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://stub/{self.n}.json",
            model_version="stub-target@1",
            verdict="pass" if value else "fail",
            target_calls=1,
            numeric_result=value,
        )


def run_cohort(rows, numeric_fn):
    with tempfile.TemporaryDirectory() as tmp:
        worker = StubWorker(numeric_fn, Path(tmp))
        store = FullScanJobStore()
        job = store.submit(
            agent_id="qa-isolation",
            scanner_version="0.5.0",
            corpus_version="qa-isolation-proof",
            model_versions={"primary": "stub-target@1", "judges": []},
            seed=0,
            registry_cases=registry_cases_from_rows(rows),
        )
        lease = store.lease(job.job_id)
        dispatch_registry_attempt(
            store,
            job.job_id,
            attempt_id=lease["attempt_id"],
            registry_rows=rows,
            workers={worker_id_for_registry_row(r): worker for r in rows},
        )
        final = store.finalize(job.job_id)
        ev = final.final_manifest["evidence_rows"]
        return {
            "n_rows": len(ev),
            "numeric_multiset": Counter(repr(r["numeric_result"]) for r in ev),
            "surface": {
                "score_contribution": sorted({bool(r["score_contribution"]) for r in ev}),
                "disposition": sorted({r["disposition"] for r in ev}),
                "bind_eligibility": final.bind_eligibility,
            },
        }


COHORTS = {
    "all_pass_1.0": lambda i, c: 1.0,
    "all_fail_0.0": lambda i, c: 0.0,
    "alternating": lambda i, c: float(i % 2),
    "huge_positive": lambda i, c: 1e9,
    "negative": lambda i, c: -1e9,
    "nan": lambda i, c: float("nan"),
}


def prove(rows, *, label: str) -> tuple[bool, list[str]]:
    """Return (property_holds, failure_reasons)."""
    reasons: list[str] = []
    results = {name: run_cohort(rows, fn) for name, fn in COHORTS.items()}

    # (a) the stub really did vary the numeric output across cohorts
    multisets = {n: r["numeric_multiset"] for n, r in results.items()}
    if multisets["all_pass_1.0"] == multisets["all_fail_0.0"]:
        reasons.append("INCONCLUSIVE: cohorts did not diverge; proof would be vacuous")

    # (b) the aggregation surface is identical no matter what the adapter emitted
    surfaces = {n: r["surface"] for n, r in results.items()}
    first = surfaces["all_pass_1.0"]
    for name, s in surfaces.items():
        if s != first:
            reasons.append(f"aggregation surface MOVED for cohort {name}: {s} != {first}")

    # (c) every row is non-contributing and report-only
    for name, s in surfaces.items():
        if s["score_contribution"] != [False]:
            reasons.append(f"cohort {name}: score_contribution not uniformly False -> {s['score_contribution']}")
        if s["disposition"] != ["report-only"]:
            reasons.append(f"cohort {name}: disposition not uniformly report-only -> {s['disposition']}")

    # (d) the boundary actively REFUSES a weight-bearing registered-case row
    for bad in ({"score_contribution": True}, {"disposition": "required"}):
        kwargs = dict(
            probe_id="hef-01", rubric_point_id="G1.1", framework="AIUC-1",
            corpus_version="x", model_version="stub-target@1", seed=0,
            status="complete", evidence_ref="evidence://x/y.json",
            acquisition_basis="target-measured", underwriting_relevance="direct",
            target_calls=1, numeric_result=1.0,
            score_contribution=False, disposition="report-only",
        )
        kwargs.update(bad)
        try:
            fse._validate_evidence_row(EvidenceAttemptRow(**kwargs))
            reasons.append(f"boundary ACCEPTED a weight-bearing row: {bad}")
        except Exception:
            pass

    print(f"  [{label}] cohorts run: {len(results)}; rows/cohort: {results['all_pass_1.0']['n_rows']}")
    print(f"  [{label}] numeric multiset all_pass : {dict(multisets['all_pass_1.0'])}")
    print(f"  [{label}] numeric multiset all_fail : {dict(multisets['all_fail_0.0'])}")
    print(f"  [{label}] aggregation surface       : {first}")
    return (not reasons), reasons


def main() -> int:
    os.environ.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    os.environ.setdefault("ARE_BUNDLE_SIGNING_KEY", "qa-isolation-key")
    rows = lane_c_rows()

    print("=" * 74)
    print("QA BEAAA-18614 — ISOLATION-PROPERTY PROOF (ruling D.4 / E)")
    print("=" * 74)
    print(f"lane-C rows under proof: {len(rows)}")
    print()

    print("PART 1 — property on the real build")
    holds, reasons = prove(rows, label="real")
    for r in reasons:
        print(f"    !! {r}")
    print(f"  -> isolation property holds: {holds}")
    print()

    # ---- PART 2: prove THIS proof is not inert -------------------------------
    print("PART 2 — sabotage self-test (does this proof detect a real leak?)")
    original_validate = fse._validate_evidence_row
    original_row_cls = fse.EvidenceAttemptRow

    class LeakyRow(original_row_cls):  # type: ignore[misc,valid-type]
        """An aggregation path that lets executed rows silently carry weight."""

        def __init__(self, **kwargs):
            if kwargs.get("numeric_result") is not None:
                kwargs["score_contribution"] = True
            super().__init__(**kwargs)

    fse._validate_evidence_row = lambda row: None  # boundary disabled
    fse.EvidenceAttemptRow = LeakyRow              # rows now carry weight
    try:
        sabotaged_holds, sabotaged_reasons = prove(rows, label="sabotaged")
    finally:
        fse._validate_evidence_row = original_validate
        fse.EvidenceAttemptRow = original_row_cls

    detected = not sabotaged_holds
    print(f"  -> proof DETECTED the sabotage: {detected}")
    for r in sabotaged_reasons[:4]:
        print(f"     detected: {r}")
    print()

    print("=" * 74)
    if holds and detected:
        print("RESULT: ISOLATION PROPERTY PROVEN, and the proof is demonstrably non-inert.")
        return 0
    if holds and not detected:
        print("RESULT: INERT PROOF — it passes even against a sabotaged aggregation path.")
        print("        Do not cite this as evidence. This is the F1 failure mode.")
        return 2
    print("RESULT: ISOLATION PROPERTY NOT PROVEN on this tree.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
