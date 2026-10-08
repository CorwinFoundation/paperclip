"""BEAAA-38957 paid Full Scan pack: pack id, hard cost cap, entitlement, concurrency."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from api.full_scan_entitlement import EntitlementError, FullScanEntitlementStore
from api.full_scan_executor import (
    COST_CAP_REACHED_REASON,
    FullScanExecutorError,
    FullScanJobStore,
    TERMINAL_JOB_STATUSES,
)
from api.full_scan_job_persistence import FullScanJobPersistence
from api.full_scan_packs import (
    PAID_PACKS,
    CaseRunResult,
    ChatResult,
    ConversationCaseRunner,
    OpenRouterChatClient,
    chat_result_from_payload,
    estimate_case_cost,
    resolve_pack,
    run_pack_attempt,
)
from api.full_scan_worker import FullScanQueueWorker
from scanners.full_scan_conversation_en import CASES
from scanners.full_scan_conversation_en_s2 import CASES_S2
from scanners.full_scan_conversation_en_s3 import CASES_S3


PACK = "full-scan-conv-en@0.1"
MODEL = "openai/gpt-4.1-mini"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 7, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class FakeRunner:
    """Every case passes; spend is a fixed fraction of the upper-bound estimate."""

    target_model = MODEL
    judge_model = MODEL

    def __init__(self, *, spend_fraction: float = 0.5, basis: str = "provider_reported") -> None:
        self.calls: Counter[str] = Counter()
        self.spend_fraction = spend_fraction
        self.basis = basis

    def run(self, case, job) -> CaseRunResult:
        self.calls[case.case_id] += 1
        est = estimate_case_cost(case, target_model=MODEL, judge_model=MODEL)
        estimated = self.basis == "estimated"
        return CaseRunResult(
            verdict="passed",
            reason="fixture",
            target_calls=len(case.turns) + len(case.pair_turns),
            judge_calls=1,
            target_cost_usd=est["target_cost_usd"] * (1 if estimated else self.spend_fraction),
            target_cost_basis=self.basis,
            judge_cost_usd=est["judge_cost_usd"] * (1 if estimated else self.spend_fraction),
            judge_cost_basis=self.basis,
            model_version=f"openrouter:{MODEL}",
            judge_model=MODEL,
        )


def _stores(db=":memory:", clock=None, **kw):
    ents = FullScanEntitlementStore(db)
    store = FullScanJobStore(
        clock=clock or Clock(),
        persistence=FullScanJobPersistence(db) if db != ":memory:" else None,
        entitlements=ents,
        **kw,
    )
    return store, ents


def _submit(store, *, tenant="tenant-a", target="target-1", pack=PACK, **kw):
    return store.submit(
        agent_id=f"agent-{target}",
        scanner_version="1.3.0",
        corpus_version="full-scan-conv-en",
        model_versions={"primary": MODEL},
        seed=0,
        pack_id=pack,
        tenant_id=tenant,
        target_id=target,
        **kw,
    )


def _grant(ents, ref, *, tenant="tenant-a", pack=PACK, cap=3.0):
    return ents.grant(
        tenant_id=tenant, pack_id=pack, max_cost_usd=cap, source="manual", external_ref=ref
    )


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-38957-test-key")


# --- pack registry -----------------------------------------------------------


def test_pack_versions_resolve_to_real_unique_cases():
    assert {k: len(resolve_pack(k)) for k in PAID_PACKS} == {
        "full-scan-conv-en@0.1": 108,
        "full-scan-conv-en@0.2": 508,
        "full-scan-conv-en@0.3": 1008,
        "full-scan-oss-jbb@1.0": 197,
        "full-scan-oss-xstest@1.0": 441,
        "full-scan-oss-strongreject@1.0": 309,
        "full-scan-oss-harmbench@1.0": 272,
        "full-scan-oss-multijail@1.0": 310,
        "full-scan-oss-cyberseceval@1.0": 205,
    }
    for pack_id in PAID_PACKS:
        ids = [c.case_id for c in resolve_pack(pack_id)]
        assert len(ids) == len(set(ids)), pack_id
    assert resolve_pack("full-scan-conv-en@0.2") == CASES + CASES_S2
    assert resolve_pack("full-scan-conv-en@0.3") == CASES + CASES_S2 + CASES_S3


def test_unknown_pack_is_rejected_at_acceptance_and_grant():
    store, ents = _stores()
    with pytest.raises(FullScanExecutorError, match="unknown_pack"):
        _submit(store, pack="full-scan-conv-en@9.9")
    with pytest.raises(FullScanExecutorError, match="unknown_pack"):
        _grant(ents, "ref-x", pack="full-scan-conv-en")


def test_pack_id_and_cap_persist_on_the_job(tmp_path):
    db = str(tmp_path / "jobs.sqlite3")
    store, ents = _stores(db)
    _grant(ents, "ref-1", cap=3.0)
    job = _submit(store)
    assert len(job.registry_cases) == 108
    reloaded = FullScanJobStore(persistence=FullScanJobPersistence(db)).status(job.job_id)
    assert (reloaded.pack_id, reloaded.tenant_id, reloaded.target_id) == (PACK, "tenant-a", "target-1")
    assert reloaded.max_cost_usd == 3.0
    assert reloaded.entitlement_id == ents.get(reloaded.entitlement_id).entitlement_id


# --- entitlement -------------------------------------------------------------


def test_paid_pack_requires_an_unconsumed_entitlement():
    store, ents = _stores()
    with pytest.raises(FullScanExecutorError, match="entitlement_required"):
        _submit(store)
    _grant(ents, "ref-1")
    job = _submit(store)
    assert ents.get(job.entitlement_id).consumed_job_id == job.job_id
    # One entitlement pays for one job.
    with pytest.raises(FullScanExecutorError, match="entitlement_required"):
        _submit(store)
    # Another tenant's grant does not admit this tenant.
    _grant(ents, "ref-b", tenant="tenant-b")
    with pytest.raises(FullScanExecutorError, match="entitlement_required"):
        _submit(store)


def test_store_without_entitlement_backend_refuses_paid_pack():
    store = FullScanJobStore(clock=Clock())
    with pytest.raises(FullScanExecutorError, match="entitlement_store_unconfigured"):
        _submit(store)


def test_grant_is_idempotent_on_external_ref_and_manual_only():
    _, ents = _stores()
    first = _grant(ents, "ref-1")
    again = _grant(ents, "ref-1")
    assert again.entitlement_id == first.entitlement_id
    assert ents.check(tenant_id="tenant-a", pack_id=PACK).entitlement_id == first.entitlement_id
    with pytest.raises(EntitlementError, match="different terms"):
        _grant(ents, "ref-1", cap=5.0)
    with pytest.raises(EntitlementError, match="unsupported entitlement source"):
        ents.grant(tenant_id="tenant-a", pack_id=PACK, max_cost_usd=3.0, source="stripe", external_ref="r")
    with pytest.raises(EntitlementError, match="positive"):
        _grant(ents, "ref-0", cap=0)


def test_job_cap_must_be_positive_and_within_the_entitlement():
    store, ents = _stores()
    _grant(ents, "ref-1", cap=3.0)
    with pytest.raises(FullScanExecutorError, match="positive"):
        _submit(store, max_cost_usd=0)
    with pytest.raises(FullScanExecutorError, match="exceeds"):
        _submit(store, max_cost_usd=3.5)
    assert ents.check(tenant_id="tenant-a", pack_id=PACK) is not None
    assert _submit(store, max_cost_usd=2.0).max_cost_usd == 2.0


def test_idempotent_accept_consumes_one_entitlement():
    store, ents = _stores()
    _grant(ents, "ref-1")
    _grant(ents, "ref-2")
    first = _submit(store, idempotency_key="order-77")
    replay = _submit(store, idempotency_key="order-77")
    assert replay.job_id == first.job_id
    assert store.job_ids() == [first.job_id]
    remaining = ents.check(tenant_id="tenant-a", pack_id=PACK)
    assert remaining is not None and remaining.consumed_job_id is None


# --- restart resume ----------------------------------------------------------


def test_restart_mid_pack_resumes_without_running_a_case_twice(tmp_path):
    db = str(tmp_path / "jobs.sqlite3")
    clock = Clock()
    store1, ents1 = _stores(db, clock)
    _grant(ents1, "ref-1")
    job = _submit(store1)
    runner1 = FakeRunner()
    worker1 = FullScanQueueWorker(store1, registry_rows=(), workers={}, pack_case_runner=runner1)
    first = worker1.run_once(max_cases=7)
    assert (first.status, first.processed_cases) == ("interrupted", 7)

    # Process dies; a new process opens the same file.
    clock.advance(10)
    store2, _ = _stores(db, clock)
    runner2 = FakeRunner()
    worker2 = FullScanQueueWorker(store2, registry_rows=(), workers={}, pack_case_runner=runner2)
    assert worker2.recover()["resumed"] == [job.job_id]
    done = worker2.run_once()
    assert done.status == "finalized" and done.processed_cases == 101

    calls = runner1.calls + runner2.calls
    assert set(calls) == {c.case_id for c in CASES}
    assert set(calls.values()) == {1}
    final = store2.status(job.job_id)
    assert final.status == "complete"
    manifest = final.final_manifest
    assert manifest["pack_id"] == PACK
    assert manifest["reconciliation"]["evidenced"] == 108
    cost = manifest["budget"]["cost"]
    assert cost["observed"]["total_usd"] == pytest.approx(cost["estimated"]["total_usd"] / 2, rel=1e-3)
    assert cost["observed"]["judge_usd"] > 0 and cost["observed"]["target_usd"] > 0
    assert cost["rows_with_estimated_cost"] == 0


# --- cost cap ----------------------------------------------------------------


def _run_to_terminal(store, job, runner):
    lease = store.lease(job.job_id)
    return run_pack_attempt(store, job.job_id, attempt_id=lease["attempt_id"], runner=runner)


def test_cost_cap_stops_at_cap_and_marks_the_rest_could_not_run():
    store, ents = _stores()
    _grant(ents, "ref-1", cap=0.02)
    job = _submit(store)
    runner = FakeRunner()
    outcome = _run_to_terminal(store, job, runner)
    final = outcome.job
    assert final.status == "incomplete_cost_cap"
    assert final.status in TERMINAL_JOB_STATUSES

    ran = [r for r in final.evidence_rows if r.status == "complete"]
    stopped = [r for r in final.evidence_rows if r.status == "could_not_run"]
    assert 0 < len(ran) < 108
    assert len(ran) + len(stopped) == 108
    assert sum(runner.calls.values()) == len(ran)
    assert {r.incomplete_reason for r in stopped} == {COST_CAP_REACHED_REASON}
    # could_not_run is never a pass and never evidenced.
    assert all(r.verdict is None and r.evidence_ref is None for r in stopped)
    assert all(r.acquisition_basis == "missing" for r in stopped)

    manifest = final.final_manifest
    cost = manifest["budget"]["cost"]
    assert cost["cost_cap_reached"] is True
    assert cost["max_cost_usd"] == 0.02
    assert cost["observed"]["total_usd"] <= 0.02
    assert manifest["reconciliation"]["terminal"] == 108
    assert manifest["reconciliation"]["evidenced"] == len(ran)
    assert manifest["reconciliation"]["by_status"]["could_not_run"] == len(stopped)
    assert manifest["acceptance_gate"]["passed"] is False
    assert manifest["execution_budget"]["max_cost_usd"] == 0.02


def test_estimated_spend_is_flagged_and_counts_against_the_cap():
    store, ents = _stores()
    _grant(ents, "ref-1", cap=3.0)
    job = _submit(store)
    final = _run_to_terminal(store, job, FakeRunner(basis="estimated")).job
    assert final.status == "complete"
    assert all(r.cost["estimated"] is True for r in final.evidence_rows)
    cost = final.final_manifest["budget"]["cost"]
    assert cost["rows_with_estimated_cost"] == 108
    assert cost["observed"]["total_usd"] == pytest.approx(cost["estimated"]["total_usd"], rel=1e-6)


# --- per-target concurrency --------------------------------------------------


def test_one_running_job_per_tenant_target_by_default():
    clock = Clock()
    store, ents = _stores(clock=clock)
    for ref in ("r1", "r2", "r3", "r4"):
        _grant(ents, ref)
    _grant(ents, "rb", tenant="tenant-b")
    jobs = []
    for tenant, target in (("tenant-a", "t1"), ("tenant-a", "t1"), ("tenant-a", "t2"), ("tenant-b", "t1")):
        jobs.append(_submit(store, tenant=tenant, target=target))
        clock.advance(1)
    job1, job2, job3, job4 = jobs

    leased = [store.lease_next() for _ in range(4)]
    assert [lease["job_id"] for lease in leased[:3]] == [job1.job_id, job3.job_id, job4.job_id]
    assert leased[3] is None
    assert store.status(job2.job_id).status == "queued"
    assert store.lease(job2.job_id) == {
        "job_id": job2.job_id, "leased": False, "reason": "target_concurrency_limit"
    }

    run_pack_attempt(store, job1.job_id, attempt_id=leased[0]["attempt_id"], runner=FakeRunner())
    assert store.lease_next()["job_id"] == job2.job_id


def test_per_target_limit_is_configurable():
    store, ents = _stores(max_running_jobs_per_target=2)
    _grant(ents, "r1")
    _grant(ents, "r2")
    _submit(store, target="t1")
    _submit(store, target="t1")
    assert store.lease_next()["leased"] and store.lease_next()["leased"]


def test_worker_without_pack_runner_leaves_paid_jobs_queued():
    store, ents = _stores()
    _grant(ents, "r1")
    job = _submit(store)
    worker = FullScanQueueWorker(store, registry_rows=(), workers={})
    assert worker.run_once().status == "idle"
    assert store.status(job.job_id).status == "queued"


# --- live runner pieces (no network) -----------------------------------------


def test_chat_usage_cost_basis():
    base = {"choices": [{"message": {"content": "hi"}}]}
    assert chat_result_from_payload(MODEL, {**base, "usage": {"cost": 0.0012}}).cost_basis == "provider_reported"
    priced = chat_result_from_payload(MODEL, {**base, "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})
    assert priced.cost_basis == "usage_tokens_priced"
    assert priced.cost_usd == pytest.approx((1000 * 0.40 + 100 * 1.60) / 1_000_000)
    silent = chat_result_from_payload(MODEL, base)
    assert (silent.cost_basis, silent.cost_usd) == ("estimated", None)


def test_openrouter_request_body_always_carries_output_ceiling(monkeypatch):
    from harness import egress

    bodies = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"cost": 0.0001},
            }

    def fake_post(url, *, headers, json, timeout):
        bodies.append(json)
        return Response()

    monkeypatch.setattr(egress, "post", fake_post)
    client = OpenRouterChatClient("test-key", max_retries=1)
    client.chat(model=MODEL, messages=[{"role": "user", "content": "target"}], max_tokens=512)
    client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": "judge"}],
        max_tokens=512,
        json_mode=True,
    )

    assert [body["max_tokens"] for body in bodies] == [512, 512]


class ScriptedChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, *, model, messages, max_tokens, json_mode=False):
        self.calls += 1
        return ChatResult(self.replies.pop(0), 0.0001, "provider_reported", 10, 10)


def test_conversation_runner_canary_judge_and_empty_reply():
    canary_case = next(c for c in CASES if c.check == "canary" and len(c.turns) == 1)
    judge_case = next(c for c in CASES if c.check == "judge" and len(c.turns) == 1)
    pair_case = next(c for c in CASES if c.check == "pair")

    judge = ScriptedChat([])
    leaked = ConversationCaseRunner(
        target=ScriptedChat([f"ok {canary_case.canary}"]), target_model=MODEL, judge=judge, judge_model=MODEL
    ).run(canary_case, None)
    assert (leaked.verdict, leaked.judge_calls, judge.calls) == ("failed", 0, 0)
    assert leaked.target_cost_basis == "provider_reported"

    judged = ConversationCaseRunner(
        target=ScriptedChat(["a helpful answer"]), target_model=MODEL,
        judge=ScriptedChat(['{"verdict": "passed", "reason": "fine"}']), judge_model=MODEL,
    ).run(judge_case, None)
    assert (judged.verdict, judged.target_calls, judged.judge_calls) == ("passed", 1, 1)

    n = len(pair_case.turns) + len(pair_case.pair_turns)
    paired = ConversationCaseRunner(
        target=ScriptedChat(["answer"] * n), target_model=MODEL,
        judge=ScriptedChat(['{"verdict": "failed", "reason": "differs"}']), judge_model=MODEL,
    ).run(pair_case, None)
    assert (paired.verdict, paired.target_calls) == ("failed", n)

    empty = ConversationCaseRunner(
        target=ScriptedChat(["  "]), target_model=MODEL, judge=ScriptedChat([]), judge_model=MODEL
    ).run(judge_case, None)
    assert empty.verdict == "could_not_run"
