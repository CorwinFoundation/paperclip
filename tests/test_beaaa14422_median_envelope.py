"""BEAAA-14422 (code-review F3) — Scorer scores the persisted envelope from
the per-dim K-replicate MEDIAN, not the last replicate.

CTO ruling on finding F3 (BEAAA-14404): confirmed real, HIGH
(reproducibility / Supreme Law). Classification: bug fix / correctness —
the v1.1.0 T5 contract already declares per-dim ``raw = median of K
replicates``; the Scorer scoring from the LAST replicate is a conformance
defect, not a semantics change. On the deterministic golden roster the fix
moves ZERO score (median == last by construction), so the N=5 calibration
gate is Δ=0. NO contract bump.

ROOT CAUSE (baseline 7f2dc613): ``scanners/live_probe_workers.py`` accumulated
``accumulated[dim_id] = entry`` (LAST-WRITE-WINS), then scored the envelope
from that last replicate — while the orchestrator reports
``ScanResult.dim_results[dim].raw = _median_of(replicates)``. For a
nondeterministic agent (median != last) the two authoritative surfaces
disagree on ``are_total``.

AC mapping (each an assertion below):

* AC#1 — FAILING-TEST-FIRST nondeterministic-agent replay: per-dim raw
  varies across K so median != last. The persisted envelope's per-dim raw
  AND ``are_total`` equal scoring from ``_median_of(replicates)`` AND equal
  ``ScanResult.dim_results[dim].raw``. FAILS on baseline (last-write-wins),
  PASSES after the fix. (Imports only baseline-stable symbols so the same
  test body demonstrates the pre-fix failure.)
* AC#2 — the Scorer uses the SHARED ``_median_of`` (same function object,
  not a reimplementation); identity holds on a randomized replicate
  multiset for BOTH K=5 and K=10 (lower-of-two-middles).
* AC#3 — representative-entry selection is deterministic (raw==median,
  lowest replicate index); the scored value is stable across a SHUFFLED
  receive order (median is order-independent).
* AC#5 — permanent guard: over randomized multisets (K=5 and K=10) the raw
  the Scorer hands ``compute_envelope`` equals the orchestrator's
  ``DimResult.raw`` for the SAME input — driving the real ``run_scorer``
  accumulation path, so any future reintroduction of divergence fails CI,
  not just the one AC#1 fixture.
"""
from __future__ import annotations

import io
import queue
import random
import tarfile
from pathlib import Path

import pytest

from api.intake_guard import HOSTED_RUNNER
from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners.live_probe_orchestrator import ScanRequest, _median_of, run_scan
from scanners.live_probe_workers import END_OF_SCAN, WorkerChannel, run_scorer
from scanners.score_runtime import ScoreRuntime
from scanners.snapshot_hash import compute_corpus_snapshot_hash

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "SECRET_TEST_DO_NOT_LEAK_b14422"
CRED_REF = "phase1-probe.v1"
EVAL_RUN_ID = "00000000-0000-0000-0000-000000000042"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def corpus_tarball(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = b'{"corpus_version":"0.1.0"}\n'
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def corpus_hash(corpus_tarball: Path) -> str:
    return compute_corpus_snapshot_hash(corpus_tarball)


@pytest.fixture
def loaded_vault():
    vault = CredentialVault()
    vault.register(CRED_REF, SECRET)
    yield vault
    vault.forget_all()


@pytest.fixture
def score_runtime() -> ScoreRuntime:
    return ScoreRuntime.from_rubric_path(REPO_ROOT / "rubric" / "rubric.yaml")


def _request(corpus_hash: str, *, k_replicates: int = 5) -> ScanRequest:
    return ScanRequest(
        scan_id="beaaa14422-scan",
        tenant_id="tenant-001",
        agent_endpoint="https://agent.example.com/api",
        credential_ref=CRED_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        seed=42,
        scan_mode="live_probe",
        k_replicates=k_replicates,
    )


def _score_inputs() -> dict[str, object]:
    return {
        "scan_id": "beaaa14422-scan",
        "agent_id": "agent-test",
        "seed": 42,
        "scanner_version": "0.3.0",
        "corpus_version": "0.1.0",
        "run_timestamp": "2026-07-15T10:00:00Z",
        "invocation_context": {"purpose": "pre_bind"},
        "prior_scan_refs": [],
    }


def _per_replicate_probe_run_fn(raws_per_replicate: list[dict[str, float]]):
    """probe_run_fn whose per-dim ``raw`` varies across replicates. The
    orchestrator calls it once per replicate; replicate ``idx`` gets a
    distinct ``evidence_uri`` so representative-entry selection is
    observable.
    """
    call_idx = {"i": 0}

    def fn(_runner):
        idx = call_idx["i"]
        rows = raws_per_replicate[idx]
        call_idx["i"] += 1
        for dim_id, raw in rows.items():
            yield dim_id, {
                "raw": float(raw),
                "evidence_uri": f"https://evidence.local/{dim_id.lower()}-r{idx}",
                "evidence_sha256": "sha256:" + "a" * 64,
            }

    return fn


def _ipc_row(dim_id: str, raw: float, replicate_idx: int) -> dict[str, object]:
    """One post-orchestrator IPC row (stamped like ``run_scan`` stamps it)
    that passes ``validate_inbound``.
    """
    return {
        "dim_id": dim_id,
        "raw": float(raw),
        "runner": HOSTED_RUNNER,
        "eval_run_id": EVAL_RUN_ID,
        "evidence_uri": f"https://evidence.local/{dim_id.lower()}-r{replicate_idx}",
        "evidence_sha256": "sha256:" + "a" * 64,
        "replicate_index": replicate_idx,
    }


class _ReplayChannel:
    """In-process channel feeding pre-built IPC rows to ``run_scorer`` and
    capturing the returned envelope. ``run_scorer`` only touches
    ``recv_row`` + ``send_score_envelope``.
    """

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._messages = list(rows) + [END_OF_SCAN]
        self._i = 0
        self.envelope_outcome: tuple | None = None

    def recv_row(self, *, timeout=None):  # noqa: ANN001
        if self._i >= len(self._messages):
            raise queue.Empty
        msg = self._messages[self._i]
        self._i += 1
        return msg

    def send_score_envelope(self, outcome, *, timeout=None):  # noqa: ANN001
        self.envelope_outcome = outcome

    def recv_score_envelope(self, *, timeout=None):  # noqa: ANN001
        return self.envelope_outcome


class _CapturingRuntime:
    """Fake ScoreRuntime that records the ``accumulated_rows`` the Scorer
    hands it (so tests can assert what got scored) and returns a minimal
    valid §2.2 envelope.
    """

    def __init__(self) -> None:
        self.captured: dict[str, dict[str, object]] | None = None

    def compute_envelope(self, *, accumulated_rows, **_kwargs):  # noqa: ANN001
        self.captured = accumulated_rows
        return {
            "agent_id": "agent-test",
            "are_total": 0,
            "band": "D",
            "confidence_below_adjudication_threshold": False,
            "corpus_version": "0.1.0",
            "evidence_refs": [],
            "model_versions": None,
            "partial_completion": False,
            "prior_scan_refs": [],
            "purpose": None,
            "rubric_version": "1.0.0",
            "run_timestamp": "2026-07-15T10:00:00Z",
            "scanner_version": "0.3.0",
            "seed": 42,
            "subscores": {},
        }


def _run_scorer_capture(rows: list[dict[str, object]]) -> _CapturingRuntime:
    runtime = _CapturingRuntime()
    channel = _ReplayChannel(rows)
    run_scorer(
        channel=channel,
        on_validated_row=lambda _d, _e: None,
        score_runtime=runtime,
        score_inputs=_score_inputs(),
        row_recv_timeout_seconds=None,
    )
    return runtime


# ---------------------------------------------------------------------------
# AC#1 — FAILING-TEST-FIRST nondeterministic-agent replay.
# ---------------------------------------------------------------------------


def test_ac1_envelope_scored_from_median_matches_scanresult(
    corpus_tarball, corpus_hash, loaded_vault, score_runtime
):
    """Nondeterministic agent: per-dim raw varies across K=5 so median !=
    last. The persisted envelope's ``are_total`` == scoring from the
    per-dim median == ``ScanResult.dim_results[dim].raw``. On baseline
    (last-write-wins) the envelope would score the LAST replicate and this
    assertion FAILS.
    """
    # Per-replicate raws. Median != last for every dim below.
    #   P1.1 -> [4,4,2,0,0] sorted [0,0,2,4,4] median 2.0 ; last 0.0
    #   P2.1 -> [1,3,3,4,0] sorted [0,1,3,3,4] median 3.0 ; last 0.0
    #   P3.1 -> [2,2,2,2,4] median 2.0 ; last 4.0
    #   P4.1 -> [0,1,2,3,4] median 2.0 ; last 4.0
    per_replicate = [
        {"P1.1": 4, "P2.1": 1, "P3.1": 2, "P4.1": 0},
        {"P1.1": 4, "P2.1": 3, "P3.1": 2, "P4.1": 1},
        {"P1.1": 2, "P2.1": 3, "P3.1": 2, "P4.1": 2},
        {"P1.1": 0, "P2.1": 4, "P3.1": 2, "P4.1": 3},
        {"P1.1": 0, "P2.1": 0, "P3.1": 4, "P4.1": 4},
    ]

    channel = WorkerChannel(max_queue=256)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(per_replicate),
        runner=MockRunner("t", responder=lambda p: "ok"),
    )
    # (Spread > band here, so status is "incomplete"; dim_results are still
    # populated with per-dim medians — only the cap-breach path empties them.)

    # Sanity: ScanResult per-dim raw is the median, and median != last.
    dim_raw = {d.dim_id: d.raw for d in result.dim_results}
    for dim_id in ("P1.1", "P2.1", "P3.1", "P4.1"):
        raws = [rep[dim_id] for rep in per_replicate]
        assert dim_raw[dim_id] == _median_of([float(r) for r in raws])
        assert dim_raw[dim_id] != float(raws[-1])  # median != last (nondeterministic)

    # Drain the SAME rows through the real Scorer -> persisted envelope.
    run_scorer(
        channel=channel,
        on_validated_row=lambda _d, _e: None,
        score_runtime=score_runtime,
        score_inputs=_score_inputs(),
        row_recv_timeout_seconds=None,
    )
    outcome = channel.recv_score_envelope(timeout=5.0)
    assert outcome[0] == "ok", outcome
    envelope = outcome[1]

    # Expected: score the per-dim MEDIAN rows (what ScanResult reports).
    median_rows = {
        dim_id: {"raw": dim_raw[dim_id], "evidence_uri": f"https://e/{dim_id}"}
        for dim_id in dim_raw
    }
    expected = score_runtime.compute_envelope(
        accumulated_rows=median_rows, **_score_inputs()
    )
    # Counterfactual: scoring the LAST replicate (the baseline bug).
    last_rows = {
        dim_id: {"raw": float(per_replicate[-1][dim_id]), "evidence_uri": f"https://e/{dim_id}"}
        for dim_id in dim_raw
    }
    last_envelope = score_runtime.compute_envelope(
        accumulated_rows=last_rows, **_score_inputs()
    )

    # Core AC#1 assertion — envelope tracks the MEDIAN, not the last replicate.
    assert envelope["are_total"] == expected["are_total"]
    assert envelope["subscores"] == expected["subscores"]
    # And the median result genuinely differs from the last-replicate result,
    # so this test is a real discriminator (would pass trivially otherwise).
    assert expected["are_total"] != last_envelope["are_total"]
    # Therefore the persisted are_total does NOT equal the baseline bug's value.
    assert envelope["are_total"] != last_envelope["are_total"]

    # Per-dim raw parity: recover scored raw from evidence_refs weight
    # (weight = raw/4) for scored dims and confirm it matches the median.
    weight_by_dim = {
        ref["rubric_point_id"]: ref["weight"]
        for ref in envelope["evidence_refs"]
        if ref.get("verdict") != "n/a"
    }
    for dim_id in ("P1.1", "P2.1", "P3.1", "P4.1"):
        assert round(weight_by_dim[dim_id] * 4.0, 6) == round(dim_raw[dim_id], 6)


# ---------------------------------------------------------------------------
# AC#2 — SHARED _median_of (identity + randomized-multiset agreement).
# ---------------------------------------------------------------------------


def test_ac2_scorer_uses_shared_median_function_object():
    """The Scorer imports the SAME ``_median_of`` object the orchestrator
    uses — not a second reimplementation.
    """
    import scanners.live_probe_orchestrator as orch
    import scanners.live_probe_workers as workers
    import scanners.replicate_median as leaf

    assert workers._median_of is orch._median_of
    assert workers._median_of is leaf._median_of


@pytest.mark.parametrize("k", [5, 10])
def test_ac2_median_identity_on_randomized_multiset(k: int):
    """On a randomized replicate multiset, the shared median equals the
    lower-of-two-middles reference for both K=5 and K=10.
    """
    rng = random.Random(20260715 + k)
    import scanners.live_probe_workers as workers

    for _ in range(200):
        raws = [float(rng.randint(0, 4)) for _ in range(k)]
        s = sorted(raws)
        expected = s[len(s) // 2] if k % 2 == 1 else s[len(s) // 2 - 1]
        assert workers._median_of(raws) == expected
        # median is always a real replicate value (audit-friendly).
        assert workers._median_of(raws) in raws


# ---------------------------------------------------------------------------
# AC#3 — deterministic representative + order-independent scored value.
# ---------------------------------------------------------------------------


def test_ac3_scored_value_stable_across_shuffled_receive_order(score_runtime):
    """The scored envelope is identical regardless of IPC receive order —
    the median is order-independent. With a unique-median multiset the
    representative entry (hence evidence_refs) is also fully stable.
    """
    # Unique median per dim so representative selection is unambiguous.
    #   P1.1 raws [0,1,2,3,4] -> median 2.0 (only replicate idx 2)
    #   P2.1 raws [4,3,2,1,0] -> median 2.0 (only replicate idx 2)
    base_rows = [
        _ipc_row("P1.1", 0, 0), _ipc_row("P1.1", 1, 1), _ipc_row("P1.1", 2, 2),
        _ipc_row("P1.1", 3, 3), _ipc_row("P1.1", 4, 4),
        _ipc_row("P2.1", 4, 0), _ipc_row("P2.1", 3, 1), _ipc_row("P2.1", 2, 2),
        _ipc_row("P2.1", 1, 3), _ipc_row("P2.1", 0, 4),
    ]

    def _envelope_for(order_seed: int) -> dict:
        rows = list(base_rows)
        random.Random(order_seed).shuffle(rows)
        channel = _ReplayChannel(rows)
        run_scorer(
            channel=channel,
            on_validated_row=lambda _d, _e: None,
            score_runtime=score_runtime,
            score_inputs=_score_inputs(),
            row_recv_timeout_seconds=None,
        )
        outcome = channel.recv_score_envelope()
        assert outcome[0] == "ok", outcome
        return outcome[1]

    envelopes = [_envelope_for(seed) for seed in (1, 2, 3, 999)]
    first = envelopes[0]
    for env in envelopes[1:]:
        assert env["are_total"] == first["are_total"]
        assert env["subscores"] == first["subscores"]
        # Unique median -> evidence_refs (incl. representative artifact_uri)
        # are byte-identical across shuffles too.
        assert env["evidence_refs"] == first["evidence_refs"]


def test_ac3_tied_median_is_lowest_index_across_shuffled_receive_order():
    """When several replicates share the median value, the representative is
    the lowest stable replicate index regardless of receive order.
    """
    from scanners.live_probe_workers import _collapse_replicates_to_median_rows

    # raws [1,2,2,2,3] median 2.0, three replicates tie on the median.
    entries = [
        {"raw": 1.0, "evidence_uri": "u0", "replicate_index": 0},
        {"raw": 2.0, "evidence_uri": "u1", "replicate_index": 1},
        {"raw": 2.0, "evidence_uri": "u2", "replicate_index": 2},
        {"raw": 2.0, "evidence_uri": "u3", "replicate_index": 3},
        {"raw": 3.0, "evidence_uri": "u4", "replicate_index": 4},
    ]
    for seed in (1, 2, 3, 999):
        shuffled = list(entries)
        random.Random(seed).shuffle(shuffled)
        collapsed = _collapse_replicates_to_median_rows({"P1.1": shuffled})
        assert collapsed["P1.1"]["raw"] == _median_of([1.0, 2.0, 2.0, 2.0, 3.0])
        assert collapsed["P1.1"]["evidence_uri"] == "u1"
        assert collapsed["P1.1"]["replicate_index"] == 1


# ---------------------------------------------------------------------------
# AC#5 — permanent guard: run_scorer collapse == orchestrator DimResult.raw.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("k", [5, 10])
def test_ac5_run_scorer_collapse_matches_orchestrator_median(
    corpus_tarball, corpus_hash, loaded_vault, k
):
    """Property guard over randomized multisets: the raw the real
    ``run_scorer`` hands ``compute_envelope`` for each dim equals the
    orchestrator's ``ScanResult.dim_results[dim].raw`` (= ``_median_of``)
    for the SAME replicate stream — for K=5 and K=10. A revert to
    last-write-wins fails this because it would hand the last replicate.
    """
    rng = random.Random(70715 + k)
    dims = ("P1.1", "P2.1", "P3.1", "P4.1")

    for _trial in range(15):
        # run_scan's finally forgets the cred_ref; re-register per trial.
        loaded_vault.register(CRED_REF, SECRET)
        per_replicate = [
            {d: float(rng.randint(0, 4)) for d in dims} for _ in range(k)
        ]
        channel = WorkerChannel(max_queue=512)
        result = run_scan(
            _request(corpus_hash, k_replicates=k),
            loaded_vault,
            channel,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=_per_replicate_probe_run_fn(per_replicate),
            runner=MockRunner("t", responder=lambda p: "ok"),
        )
        dim_raw = {dr.dim_id: dr.raw for dr in result.dim_results}

        # Drain the same channel through the real run_scorer accumulation
        # path; capture what it would score.
        runtime = _CapturingRuntime()
        run_scorer(
            channel=channel,
            on_validated_row=lambda _d, _e: None,
            score_runtime=runtime,
            score_inputs=_score_inputs(),
            row_recv_timeout_seconds=None,
        )
        assert runtime.captured is not None
        for d in dims:
            scored = float(runtime.captured[d]["raw"])
            assert scored == dim_raw[d]  # envelope raw == ScanResult raw
            assert scored == _median_of([rep[d] for rep in per_replicate])
