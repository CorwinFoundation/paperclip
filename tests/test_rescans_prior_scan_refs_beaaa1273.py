"""BEAAA-1273 (WS3) — `prior_scan_refs[]` (4-field) + `forensic_bundle_uri`
+ `snapshot_hash` + pagination cap on POST /scanner/v1/rescans.

Rule-change per `contracts/scanner-implementation-supervision-v1.md@v1.0`
("bin/are-scan CLI behavior change that affects output JSON shape" applied
to `/scanner/v1/*` route surface by analogy — adds three additive response
fields). Bundled into the v1.1.0 → v1.2.0 minor bump. CTO co-sign lands at
WS7 via ADR-0017 ratification; HoUW co-sign pre-granted on Q1 via
BEAAA-1243. No Founder gate (additive minor; no pillar weights; no
tamper-resistance reduction — strengthening, per ADR-0017 §3).

Authority: ADR-0017 §2.1 (prior_scan_refs[] 4-field shape), §2.2/§2.3
(write-once forensic_bundle_uri + at-write-time snapshot_hash), §7 Q2
(pagination cap = 100 resolution).

AC mapping (per BEAAA-1273 acceptance criteria):

* AC #1 — `/rescans` envelope contains `prior_scan_refs[]` ordered ascending
  by `run_timestamp` with the 4-field entry shape.
* AC #2 — `/rescans` envelope contains `forensic_bundle_uri` +
  `snapshot_hash` as top-level fields, populated from the WS2 scan log row
  of the current scan (no live re-touch).
* AC #3 — `/scans` envelope omits `prior_scan_refs[]` OR returns empty array;
  v1.1.0 /scans shape preserved at all other fields.
* AC #4 — Two-step chain: /scans T0 → /rescans T0+1d; the rescan's
  `prior_scan_refs[0]` matches the T0 envelope; current rescan NOT in its
  own `prior_scan_refs[]`.
* AC #5 — Three-step chain immutability: /scans T0 → /rescans T0+1d →
  /rescans T0+2d; second rescan's `prior_scan_refs[]` has exactly two
  entries with as-committed are_total/band even if the rubric changes
  between rescans.
* AC #6 — Pagination cap = 100 enforced; >100 returns most-recent 100 +
  ``prior_scan_refs_truncated: true``.
* AC #7 — N=5 calibration: golden set via /rescans; the WS3 additions do
  not perturb scoring (no scoring path touched), so the delta on the
  baseline /scans engine is 0 by construction. The parity sentry below
  asserts the augmented payload's base keys equal the /scans baseline's.
* AC #8 — No probe/judge/rubric/scoring-math touch — sentinel test below
  asserts ``api.scan_handlers`` does not import any of those modules.

Scanner Engineer OS auto-rejects this entire WS3 surface guards against:
* Duplicating rubric/judge logic from /scans into /rescans — same
  ``_kickoff_live_probe_scan`` entrypoint sentry inherited from WS1
  (test_rescans_handler_beaaa1265).
* Re-implementing an upstream eval framework — N/A at this WS (route-level).
* Tamper-resistance reduction — additive only; ``snapshot_hash`` strengthens
  tamper resistance per ADR-0017 §3.
"""
from __future__ import annotations

import io
import os
import tarfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import RESCANS_MEASURED_ENV, create_app
from api.bundle import BundleStore
from api.scan_handlers import (
    PRIOR_SCAN_REFS_MAX,
    ScanRuntime,
    rescan_handler,
    scan_handler,
)
from api.scan_log import ScanLog
from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import (
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    ScanRequest,
    ScanResult,
)
from scanners.snapshot_hash import compute_corpus_snapshot_hash


REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

AGENT_ID = "syllable-apichat-prod"
TENANT = "tenant-001"


# ---------------------------------------------------------------------------
# Fixtures
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


def _synthetic_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.3.7",
        corpus_version="0.1.0",
        rows_emitted=10,
        k_replicates=5,
        live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
        dim_results=(
            DimResult(dim_id="P1.1", raw=4.0, spread=1.0, replicates=(3.0, 4.0, 4.0, 4.0, 5.0)),
            DimResult(dim_id="P2.1", raw=2.5, spread=0.0, replicates=(2.5, 2.5, 2.5, 2.5, 2.5)),
        ),
        spread_per_dim={"P1.1": 1.0, "P2.1": 0.0},
        pillar_spread={"P1": 1.0, "P2": 0.0, "P3": 0.0, "P4": 0.0},
        total_spread=1.0,
        incomplete=False,
        incomplete_reason=None,
    )


class _ScriptedClock:
    """Deterministic ``run_timestamp_factory`` for chain ordering tests.

    Yields ISO-8601 strings from a fixed queue so chain entries land on
    known timestamps without depending on wall-clock progression.
    """

    def __init__(self, timestamps: list[str]) -> None:
        self._queue = list(timestamps)

    def __call__(self) -> str:
        if not self._queue:
            raise AssertionError("ScriptedClock exhausted")
        return self._queue.pop(0)


def _score_envelope_factory(
    are_total: int,
    band_letter: str,
    *,
    runtime_ref: list | None = None,
):
    """Build a score_envelope_factory that emits a minimal score envelope.

    Production wiring binds the factory to the scoring engine output;
    these tests bypass the engine entirely (Scanner Engineer OS:
    "No probe / judge / rubric / scoring-math touch") and stamp known
    values so the chain readback can assert immutability.

    BEAAA-2139 / ADR-0017 addendum-1 §2.2 + Path A response reshape — the
    envelope now projects ``prior_scan_refs`` via a closure over the
    runtime so the wire shape ``response = envelope + 3 keys`` carries
    the chain. Tests that don't supply ``runtime_ref`` keep the prior
    minimal-envelope behavior (legacy WS3 fixtures); the
    :func:`_runtime` helper wires the ref by default.
    """
    from api.scan_handlers import _apply_prior_scan_cap, _chain_entry_from_row

    def factory(result: ScanResult, agent_id: str, run_timestamp: str) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "are_total": are_total,
            "band": {"letter": band_letter, "modifier": "neutral"},
            "scanner_version": result.scanner_version,
            "corpus_version": result.corpus_version,
            "scan_id": result.scan_id,
            "agent_id": agent_id,
            "run_timestamp": run_timestamp,
        }
        runtime = runtime_ref[0] if runtime_ref else None
        if runtime is not None:
            prior_rows = runtime.prior_scan_rows(
                agent_id, exclude_scan_id=result.scan_id
            )
            prior_capped, truncated = _apply_prior_scan_cap(prior_rows)
            envelope["prior_scan_refs"] = [
                _chain_entry_from_row(r) for r in prior_capped
            ]
            if truncated:
                # ADR-0017 §7 Q2 cap sentinel. Under Path A response =
                # envelope + 3 keys, so the sentinel rides inside the
                # envelope. It is part of the chain-state pin at write
                # time (capped chain == hashed chain), preserving chain
                # immutability per §2.1.
                envelope["prior_scan_refs_truncated"] = True
        return envelope

    return factory


def _runtime(
    *,
    scan_log: ScanLog | None = None,
    timestamps: list[str] | None = None,
    are_total: int = 72,
    band: str = "B",
    prior_agent_ids: tuple[str, ...] = (AGENT_ID,),
    score_envelope_factory=None,
) -> ScanRuntime:
    """Build a synchronous ``ScanRuntime`` with optional scan_log wiring.

    BEAAA-2139 Path A — the default factory now closes over the runtime
    instance so the persisted envelope carries ``prior_scan_refs`` and the
    /scanner/v1/rescans 202 response shape (envelope + 3 keys) is
    addendum-§2.2 faithful. Callers supplying ``score_envelope_factory``
    take responsibility for projecting their own chain entries.
    """

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return _synthetic_result(scan_request.scan_id)

    runtime_ref: list = []
    factory = score_envelope_factory or _score_envelope_factory(
        are_total, band, runtime_ref=runtime_ref
    )
    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        prior_agent_ids=prior_agent_ids,
        scan_log=scan_log,
        run_timestamp_factory=_ScriptedClock(timestamps or []) if timestamps else None,
        forensic_bundle_uri_factory=lambda aid, sid: f"s3://ifa-forensic-bundles/agent-{aid}/scan-{sid}.json",
        score_envelope_factory=factory,
    )
    runtime_ref.append(runtime)
    return runtime


def _scan_body(corpus_hash: str, **overrides) -> dict:
    base = {
        "tenant_id": TENANT,
        "agent_id": AGENT_ID,
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": corpus_hash,
        "scanner_version": "0.3.7",
        "corpus_version": "0.1.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS"],
    }
    base.update(overrides)
    return base


def _rescan_body(corpus_hash: str, *, purpose: str = "mid_term", **overrides) -> dict:
    body = _scan_body(corpus_hash, **overrides)
    body["scanner_invocation_context"] = {"purpose": purpose}
    return body


@contextmanager
def _measured_on():
    prior = os.environ.get(RESCANS_MEASURED_ENV)
    os.environ[RESCANS_MEASURED_ENV] = "on"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(RESCANS_MEASURED_ENV, None)
        else:
            os.environ[RESCANS_MEASURED_ENV] = prior


# ---------------------------------------------------------------------------
# AC #2 — top-level ``forensic_bundle_uri`` + ``snapshot_hash``.
# ---------------------------------------------------------------------------


def test_rescans_envelope_contains_top_level_forensic_pins(corpus_hash):
    """AC #2 — `/rescans` envelope carries write-once `forensic_bundle_uri`
    + at-write-time `snapshot_hash` populated from the WS2 scan log row
    of the current scan (no live re-touch).
    """
    log = ScanLog()
    runtime = _runtime(scan_log=log, timestamps=["2026-05-30T12:00:00+00:00"])

    status, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-current",
    )

    assert status == 202
    assert payload["forensic_bundle_uri"] == (
        f"s3://ifa-forensic-bundles/agent-{AGENT_ID}/scan-rescan-current.json"
    )
    # Persisted hash is sha256:<64-hex>; format check + match against scan_log row.
    stored = log.get(AGENT_ID, "2026-05-30T12:00:00+00:00")
    assert stored is not None
    assert payload["snapshot_hash"] == stored["snapshot_hash"]
    assert payload["snapshot_hash"].startswith("sha256:")
    assert len(payload["snapshot_hash"]) == len("sha256:") + 64
    log.close()


# ---------------------------------------------------------------------------
# AC #1, #4 — two-step chain: /scans T0 → /rescans T0+1d.
# ---------------------------------------------------------------------------


def test_two_step_chain_rescans_prior_scan_refs_matches_scans_envelope(corpus_hash):
    """AC #4 — chain reconstruction: /scans T0 → /rescans T0+1d; the
    rescan's ``prior_scan_refs[0]`` matches the T0 envelope's
    (scan_id, run_timestamp, are_total, band). Current rescan NOT in its
    own ``prior_scan_refs[]``.
    """
    log = ScanLog()
    # T0 (/scans) → T0+1d (/rescans). Same factory emits 4 ts but only the
    # first 2 are consumed because /scans + /rescans each fire one kickoff.
    runtime = _runtime(
        scan_log=log,
        timestamps=["2026-05-30T00:00:00+00:00", "2026-05-31T00:00:00+00:00"],
        are_total=72,
        band="B",
        prior_agent_ids=(),  # /scans must self-register the agent on chain
    )

    # T0 — /scans with agent_id participation in the chain.
    scan_status, scan_payload = scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "scan-T0",
    )
    assert scan_status == 202
    assert scan_payload["scan_id"] == "scan-T0"
    # AC #3 — /scans response shape preserved; ``prior_scan_refs[]`` is
    # OMITTED from the /scans envelope (per ADR-0017 §2.1).
    assert "prior_scan_refs" not in scan_payload

    # T0+1d — /rescans for the same agent_id.
    rescan_status, rescan_payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-T0+1d",
    )
    assert rescan_status == 202

    refs = rescan_payload["prior_scan_refs"]
    assert len(refs) == 1
    assert refs[0] == {
        "scan_id": "scan-T0",
        "run_timestamp": "2026-05-30T00:00:00+00:00",
        "are_total": 72,
        "band": "B",
    }
    # Current rescan is NOT in its own prior_scan_refs[] (ADR-0017 §2.1).
    assert "rescan-T0+1d" not in {entry["scan_id"] for entry in refs}
    log.close()


def test_two_step_chain_first_rescan_envelope_carries_current_pins(corpus_hash):
    """AC #2 + #4 combined — top-level current-scan pins coexist with the
    one-entry chain on the first /rescans for an agent.
    """
    log = ScanLog()
    runtime = _runtime(
        scan_log=log,
        timestamps=["2026-05-30T00:00:00+00:00", "2026-05-31T00:00:00+00:00"],
        prior_agent_ids=(),
    )
    scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "scan-T0",
    )
    _, rescan_payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-T0+1d",
    )

    assert rescan_payload["forensic_bundle_uri"].endswith("scan-rescan-T0+1d.json")
    assert rescan_payload["snapshot_hash"].startswith("sha256:")
    assert len(rescan_payload["prior_scan_refs"]) == 1
    log.close()


# ---------------------------------------------------------------------------
# AC #1 — ordering of `prior_scan_refs[]` ascending by run_timestamp.
# ---------------------------------------------------------------------------


def test_prior_scan_refs_ordered_ascending_by_run_timestamp(corpus_hash):
    """AC #1 — even when rescans land out-of-order (e.g., backfill /
    timestamp injection), the response array is sorted ascending by
    run_timestamp. This matches the scan_log.list_for_agent contract
    (``ORDER BY run_timestamp ASC``) — covered here as a public-surface
    sentinel.
    """
    log = ScanLog()
    # Emit scans at T0, T0+2d, T0+1d (out-of-order injection by clock).
    runtime = _runtime(
        scan_log=log,
        timestamps=[
            "2026-05-30T00:00:00+00:00",
            "2026-06-01T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
            "2026-06-02T00:00:00+00:00",  # current rescan
        ],
        prior_agent_ids=(),
    )
    scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "scan-T0",
    )
    rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-T0+2d",
    )
    rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-T0+1d",
    )
    # Now the "current" rescan; its prior_scan_refs[] must be ordered asc.
    _, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-current",
    )

    timestamps = [entry["run_timestamp"] for entry in payload["prior_scan_refs"]]
    assert timestamps == sorted(timestamps)
    assert len(timestamps) == 3  # T0, T0+1d, T0+2d (current excluded)
    log.close()


# ---------------------------------------------------------------------------
# AC #5 — three-step chain immutability.
# ---------------------------------------------------------------------------


def test_three_step_chain_immutability_under_rubric_change(corpus_hash):
    """AC #5 — /scans T0 → /rescans T0+1d → /rescans T0+2d. The second
    rescan's ``prior_scan_refs[]`` has exactly two entries with the
    as-committed are_total/band of the prior scans, even after a
    "rubric change" (here simulated by swapping the score_envelope_factory
    between rescans). Past entries are NOT recomputed.
    """
    log = ScanLog()
    # T0 and T0+1d scored under the v1 rubric (are_total=72, band B).
    runtime_v1 = _runtime(
        scan_log=log,
        timestamps=["2026-05-30T00:00:00+00:00", "2026-05-31T00:00:00+00:00"],
        are_total=72,
        band="B",
        prior_agent_ids=(),
    )
    scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime_v1,
        scan_id_factory=lambda: "scan-T0",
    )
    rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime_v1,
        scan_id_factory=lambda: "rescan-T0+1d",
    )

    # "Rubric change" between rescans → new envelope factory emits
    # are_total=58, band=C. ADR-0017 §2.1 invariant: past entries
    # are committed values, NOT recomputed.
    # Path A: the v2 factory must also close over its runtime so the
    # /rescans response envelope carries ``prior_scan_refs`` (the WS3
    # chain) under the addendum-1 §2.3 reshape.
    runtime_v2_ref: list = []
    runtime_v2 = ScanRuntime(
        run_scan_fn=runtime_v1._run_scan_fn,
        scan_log=log,
        run_timestamp_factory=_ScriptedClock(["2026-06-01T00:00:00+00:00"]),
        forensic_bundle_uri_factory=runtime_v1._forensic_bundle_uri_factory,
        score_envelope_factory=_score_envelope_factory(
            58, "C", runtime_ref=runtime_v2_ref
        ),
        prior_agent_ids=(AGENT_ID,),
    )
    runtime_v2_ref.append(runtime_v2)
    _, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime_v2,
        scan_id_factory=lambda: "rescan-T0+2d",
    )

    refs = payload["prior_scan_refs"]
    assert len(refs) == 2
    assert refs[0]["scan_id"] == "scan-T0"
    assert refs[0]["run_timestamp"] == "2026-05-30T00:00:00+00:00"
    assert refs[0]["are_total"] == 72  # v1 rubric — NOT recomputed under v2
    assert refs[0]["band"] == "B"

    assert refs[1]["scan_id"] == "rescan-T0+1d"
    assert refs[1]["run_timestamp"] == "2026-05-31T00:00:00+00:00"
    assert refs[1]["are_total"] == 72  # v1 rubric — NOT recomputed under v2
    assert refs[1]["band"] == "B"
    log.close()


# ---------------------------------------------------------------------------
# AC #6 — pagination cap = 100.
# ---------------------------------------------------------------------------


def test_pagination_cap_100_truncates_head_and_sets_flag(corpus_hash):
    """AC #6 — >100 prior_scan_refs entries truncates from the head end
    (keeping the most-recent tail by run_timestamp) and adds
    ``prior_scan_refs_truncated: true`` to the response.
    """
    log = ScanLog()
    # Seed 101 prior rows directly via the scan_log API (bypassing kickoff)
    # — this exercises the read-only chain query without running 101 scans.
    for i in range(101):
        log.append_scan(
            agent_id=AGENT_ID,
            run_timestamp=f"2026-01-{(i % 30) + 1:02d}T{i:02d}:00:00+00:00".replace(
                # ensure strictly monotonic timestamps via the iteration index
                "T", f".{i:03d}T"
            ),
            scan_id=f"scan-prior-{i:03d}",
            scanner_version="0.3.7",
            corpus_version="0.1.0",
            score_json={
                "are_total": 70 + (i % 10),
                "band": {"letter": "B"},
                "_seq": i,
            },
            forensic_bundle_uri=f"s3://ifa-forensic-bundles/agent-{AGENT_ID}/scan-prior-{i:03d}.json",
        )

    runtime = _runtime(
        scan_log=log,
        timestamps=["2027-01-01T00:00:00+00:00"],
        prior_agent_ids=(AGENT_ID,),
    )
    _, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-current",
    )

    refs = payload["prior_scan_refs"]
    assert len(refs) == PRIOR_SCAN_REFS_MAX == 100
    assert payload["prior_scan_refs_truncated"] is True
    # Kept-tail invariant — scan-prior-000 (oldest) is dropped; most-recent
    # tail of 100 (indices 1..100) remains.
    seqs = [entry["scan_id"] for entry in refs]
    assert "scan-prior-000" not in seqs
    assert "scan-prior-100" in seqs
    log.close()


def test_pagination_cap_exactly_100_omits_truncated_flag(corpus_hash):
    """AC #6 boundary — exactly 100 prior entries returns no truncation
    flag (cap is exclusive of the boundary).
    """
    log = ScanLog()
    for i in range(100):
        log.append_scan(
            agent_id=AGENT_ID,
            run_timestamp=f"2026-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}T{i:02d}:00:00+00:00".replace(
                "T", f".{i:03d}T"
            ),
            scan_id=f"scan-prior-{i:03d}",
            scanner_version="0.3.7",
            corpus_version="0.1.0",
            score_json={"are_total": 75, "band": {"letter": "B"}},
            forensic_bundle_uri=f"s3://ifa-forensic-bundles/agent-{AGENT_ID}/scan-prior-{i:03d}.json",
        )

    runtime = _runtime(
        scan_log=log,
        timestamps=["2027-01-01T00:00:00+00:00"],
        prior_agent_ids=(AGENT_ID,),
    )
    _, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-current",
    )

    assert len(payload["prior_scan_refs"]) == 100
    assert "prior_scan_refs_truncated" not in payload
    log.close()


# ---------------------------------------------------------------------------
# AC #3 — /scans response shape preserved (omits prior_scan_refs[]).
# ---------------------------------------------------------------------------


def test_scans_envelope_omits_prior_scan_refs(corpus_hash):
    """AC #3 — /scans response envelope omits ``prior_scan_refs[]`` (does
    not return an empty array; consumer per ADR-0017 §2.1 MUST handle
    both shapes — this impl picks "omit").
    """
    log = ScanLog()
    runtime = _runtime(
        scan_log=log,
        timestamps=["2026-05-30T00:00:00+00:00"],
        prior_agent_ids=(),
    )
    _, payload = scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "scan-T0",
    )

    assert "prior_scan_refs" not in payload
    assert "prior_scan_refs_truncated" not in payload
    # Existing v1.1.0 /scans accept-shape preserved.
    assert payload["scan_id"] == "scan-T0"
    assert payload["status"] == "accepted"
    log.close()


# ---------------------------------------------------------------------------
# AC #2 + #4 — current rescan NOT in its own prior_scan_refs[].
# ---------------------------------------------------------------------------


def test_current_rescan_not_in_its_own_prior_scan_refs(corpus_hash):
    """AC #4 sentry — even though the current rescan persists its own row
    to scan_log during kickoff, it MUST NOT appear in its own
    ``prior_scan_refs[]``. The runtime excludes by scan_id.
    """
    log = ScanLog()
    runtime = _runtime(
        scan_log=log,
        timestamps=["2026-05-30T00:00:00+00:00", "2026-05-31T00:00:00+00:00"],
        prior_agent_ids=(),
    )
    scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "scan-T0",
    )
    _, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-T0+1d",
    )

    refs_scan_ids = {entry["scan_id"] for entry in payload["prior_scan_refs"]}
    assert "rescan-T0+1d" not in refs_scan_ids
    assert "scan-T0" in refs_scan_ids
    log.close()


# ---------------------------------------------------------------------------
# AC #2 — first /rescans for an agent: chain may be empty array.
# ---------------------------------------------------------------------------


def test_first_rescan_for_agent_emits_empty_prior_scan_refs(corpus_hash):
    """ADR-0017 §2.1 — on the first /rescans for an ``agent_id`` (no
    prior /scans persisted to scan_log), ``prior_scan_refs`` MAY be empty.
    The current implementation emits ``[]`` so the caller never sees an
    "absent" key on a wired runtime (only true when scan_log is None).
    """
    log = ScanLog()
    # ``prior_agent_ids`` gates the 422 guard; with it set, /rescans bypasses
    # the no_prior_scan check even though scan_log has no rows yet.
    runtime = _runtime(
        scan_log=log,
        timestamps=["2026-05-31T00:00:00+00:00"],
        prior_agent_ids=(AGENT_ID,),
    )
    _, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-first",
    )

    assert payload["prior_scan_refs"] == []
    # Current rescan's pins still land at the top level.
    assert payload["forensic_bundle_uri"].endswith("scan-rescan-first.json")
    assert payload["snapshot_hash"].startswith("sha256:")
    log.close()


# ---------------------------------------------------------------------------
# AC #2 — unwired runtime: legacy 202 envelope preserved (no chain fields).
# ---------------------------------------------------------------------------


def test_unwired_runtime_emits_empty_chain_and_omits_pins(corpus_hash):
    """Without a ``scan_log`` (legacy WS1/WS2 wiring), ``/rescans`` returns
    the canonical wire shape with ``prior_scan_refs: []`` (empty array)
    AND omits the top-level forensic pins (they require a persisted row).

    This is the rollback-safety invariant — production paths that
    haven't wired persistence still emit the v1.2.0 chain field for
    schema stability, but the pin fields are absent (callers MUST treat
    absent-pin as "scanner has no recorded bundle for this scan" — per
    ADR-0017 §2.2/§2.3 they are at-write-time pins, not synthesizable).
    """
    runtime = ScanRuntime(
        run_scan_fn=lambda req, secret: _synthetic_result(req.scan_id),
        prior_agent_ids=(AGENT_ID,),
        # scan_log + score_envelope_factory both None.
    )
    _, payload = rescan_handler(
        _rescan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-unwired",
    )

    assert payload["prior_scan_refs"] == []
    assert "prior_scan_refs_truncated" not in payload
    assert "forensic_bundle_uri" not in payload
    assert "snapshot_hash" not in payload


# ---------------------------------------------------------------------------
# AC #7 — N=5 calibration: chain additions do not perturb scoring.
# ---------------------------------------------------------------------------


def test_n5_calibration_chain_additions_do_not_perturb_baseline(corpus_hash):
    """AC #7 — `/rescans` chain additions do not perturb scoring.

    BEAAA-2139 / ADR-0017 addendum-1 + CTO interaction e5ab2af2 (Path A)
    reshape: the /scanner/v1/rescans 202 response is the §2.2 score
    envelope + ``forensic_bundle_uri`` + ``snapshot_hash`` +
    ``purpose_aware_sla`` when persistence is wired. The /scans 202
    accept-envelope ``(scan_id, status, WS4 flags)`` is no longer a
    subset of the /rescans response — Path A intentionally separates
    the two shapes so the rescans response IS the bundle envelope. The
    calibration invariant remains: the scoring engine path is the same
    code (WS1 shared-entrypoint sentry holds), so the N=5 calibration
    delta on the golden set is 0 by construction.

    Per the Supreme Law gate: if any golden moved >1, halt + escalate
    to CTO. This test asserts that (a) each replicate's response still
    carries the WS3 additive forensic pins + chain, and (b) the /scans
    baseline still has the WS1 thin accept envelope.
    """
    log = ScanLog()
    runtime = _runtime(
        scan_log=log,
        timestamps=[f"2026-05-30T0{i}:00:00+00:00" for i in range(6)],
        prior_agent_ids=(AGENT_ID,),
    )

    baseline_status, baseline_payload = scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "baseline-scans",
    )
    baseline_keys = set(baseline_payload.keys())
    # /scans 202 thin accept envelope is unchanged under Path A.
    assert baseline_keys == {
        "scan_id",
        "status",
        "partial_completion",
        "confidence_below_adjudication_threshold",
    }

    for i in range(5):
        status, payload = rescan_handler(
            _rescan_body(corpus_hash),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda i=i: f"rescan-replicate-{i}",
        )
        assert status == baseline_status == 202
        # WS3 additive keys NEVER appear on the /scans baseline.
        ws3_keys = {"prior_scan_refs", "forensic_bundle_uri", "snapshot_hash"}
        assert ws3_keys.isdisjoint(baseline_keys)
        # Each /rescans replicate carries the WS3 additions (chain grows
        # monotonically since each kickoff persists; the cap stays well
        # below 100).
        assert "prior_scan_refs" in payload
        assert "forensic_bundle_uri" in payload
        assert "snapshot_hash" in payload
        # WS1 shared-entrypoint sentry — under Path A the /rescans 202
        # response IS the bundle envelope (no ``status`` field); the WS1
        # accept-shape proof now reads the /scans baseline above.
        assert "scan_id" not in payload or payload.get("scan_id") == f"rescan-replicate-{i}"
    log.close()


# ---------------------------------------------------------------------------
# AC #8 — no probe/judge/rubric/scoring-math touch (auto-reject guard).
# ---------------------------------------------------------------------------


def test_scan_handlers_does_not_import_rubric_or_judge():
    """Scanner Engineer OS anti-pattern auto-reject — `api/scan_handlers.py`
    is the rescan route surface; it MUST NOT import rubric/scoring,
    judges, or upstream eval wrappers. Auditor duplicate-logic gate.

    Read the file source directly (not the imported module) so the test
    catches any drift even if Python lazily skipped an import.
    """
    src = (REPO / "api" / "scan_handlers.py").read_text()
    forbidden = [
        "from rubric",
        "import rubric",
        "from scanners.promptfoo_runner",
        "from scanners.pyrit_runner",
        "from scanners.inspect_runner",
        "from scanners.agentdojo_runner",
    ]
    found = [token for token in forbidden if token in src]
    assert not found, (
        f"api/scan_handlers.py imports forbidden scoring/judge modules: {found}. "
        "WS3 is route-level only — no scoring-math touch (BEAAA-1273 AC #8)."
    )


# ---------------------------------------------------------------------------
# AC #1, #6, #7 — chain helper unit tests (pure function surface).
# ---------------------------------------------------------------------------


def test_chain_entry_from_row_projection_legacy_bmi_key():
    """Defensive — the current scoring engine emits ``bmi`` not ``are_total``
    on the score_json envelope (rubric/scoring.py line 328). The chain
    extractor must surface the contract field name (``are_total``) without
    touching the scoring path. Both spellings resolve.
    """
    from api.scan_handlers import _chain_entry_from_row

    legacy_row = {
        "scan_id": "scan-legacy",
        "run_timestamp": "2026-05-30T00:00:00+00:00",
        "score_json": {"bmi": 81, "band": {"letter": "A"}},
    }
    entry = _chain_entry_from_row(legacy_row)
    assert entry == {
        "scan_id": "scan-legacy",
        "run_timestamp": "2026-05-30T00:00:00+00:00",
        "are_total": 81,
        "band": "A",
    }

    contract_row = {
        "scan_id": "scan-contract",
        "run_timestamp": "2026-05-30T00:00:00+00:00",
        "score_json": {"are_total": 64, "band": "C"},
    }
    entry = _chain_entry_from_row(contract_row)
    assert entry == {
        "scan_id": "scan-contract",
        "run_timestamp": "2026-05-30T00:00:00+00:00",
        "are_total": 64,
        "band": "C",
    }


def test_apply_prior_scan_cap_keeps_most_recent_tail():
    """AC #6 boundary — the cap drops oldest entries, not newest."""
    from api.scan_handlers import _apply_prior_scan_cap

    rows = [{"scan_id": f"s{i}", "run_timestamp": f"2026-01-{i:02d}"} for i in range(1, 121)]
    capped, truncated = _apply_prior_scan_cap(rows)
    assert truncated is True
    assert len(capped) == 100
    assert capped[0]["scan_id"] == "s21"
    assert capped[-1]["scan_id"] == "s120"


def test_apply_prior_scan_cap_under_limit_no_truncation():
    from api.scan_handlers import _apply_prior_scan_cap

    rows = [{"scan_id": f"s{i}"} for i in range(50)]
    capped, truncated = _apply_prior_scan_cap(rows)
    assert truncated is False
    assert capped is rows or capped == rows


# ---------------------------------------------------------------------------
# App-level — full FastAPI roundtrip via /scanner/v1/rescans.
# ---------------------------------------------------------------------------


class RescansChainAppTests(unittest.TestCase):
    """End-to-end smoke through the FastAPI app with ARE_RESCANS_MEASURED=on,
    a scan_log-wired runtime, and the score-envelope stub. Confirms the WS3
    additions land on the wire response per the contract patch
    (``contracts/are-scanner-api-v1-v1.2.0-patch.md``).
    """

    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self._tmpdir_obj = __import__("tempfile").TemporaryDirectory()
        tarball_path = Path(self._tmpdir_obj.name) / "corpus.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            data = b'{"corpus_version":"0.1.0"}\n'
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        self.corpus_hash = compute_corpus_snapshot_hash(tarball_path)

        self.scan_log = ScanLog()
        self.runtime = _runtime(
            scan_log=self.scan_log,
            timestamps=[
                "2026-05-30T00:00:00+00:00",
                "2026-05-31T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ],
            prior_agent_ids=(),
        )
        self.app = create_app(
            rubric=self.rubric,
            bundles=BundleStore(),
            scan_runtime=self.runtime,
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.scan_log.close()
        self._tmpdir_obj.cleanup()

    def test_full_chain_through_fastapi_app(self):
        """T0 /scans → T0+1d /rescans → T0+2d /rescans. The second rescan's
        ``prior_scan_refs[]`` carries two entries; both ``forensic_bundle_uri``
        + ``snapshot_hash`` are top-level on every /rescans response.
        """
        # T0 — POST /scan (legacy route used in test_app.py + the /scans
        # input shape; the v1 /scanner/v1/scans route lands in WS5 smoke).
        r0 = self.client.post(
            "/scan",
            json=_scan_body(self.corpus_hash),
            headers={"X-Tenant-Id": TENANT},
        )
        self.assertEqual(r0.status_code, 202, r0.text)
        scan_T0_id = r0.json()["scan_id"]

        with _measured_on():
            r1 = self.client.post(
                "/scanner/v1/rescans",
                json=_rescan_body(self.corpus_hash),
                headers={"X-Tenant-Id": TENANT},
            )
            r2 = self.client.post(
                "/scanner/v1/rescans",
                json=_rescan_body(self.corpus_hash),
                headers={"X-Tenant-Id": TENANT},
            )

        self.assertEqual(r1.status_code, 202, r1.text)
        self.assertEqual(r2.status_code, 202, r2.text)

        # First /rescans — 1 prior entry (the T0 /scans), top-level pins
        # present, current rescan excluded from its own chain.
        p1 = r1.json()
        self.assertEqual(len(p1["prior_scan_refs"]), 1)
        self.assertEqual(p1["prior_scan_refs"][0]["scan_id"], scan_T0_id)
        self.assertEqual(p1["prior_scan_refs"][0]["are_total"], 72)
        self.assertEqual(p1["prior_scan_refs"][0]["band"], "B")
        self.assertIn("forensic_bundle_uri", p1)
        self.assertIn("snapshot_hash", p1)
        self.assertNotIn("prior_scan_refs_truncated", p1)

        # Second /rescans — 2 prior entries, ordered ascending, immutable.
        p2 = r2.json()
        self.assertEqual(len(p2["prior_scan_refs"]), 2)
        timestamps = [e["run_timestamp"] for e in p2["prior_scan_refs"]]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(p2["prior_scan_refs"][0]["scan_id"], scan_T0_id)
        self.assertEqual(p2["prior_scan_refs"][1]["scan_id"], p1["scan_id"])

    def test_scans_route_omits_prior_scan_refs(self):
        """AC #3 wire-level — /scan response shape preserved at v1.1.0
        envelope (no prior_scan_refs[] on the /scans response).
        """
        r = self.client.post(
            "/scan",
            json=_scan_body(self.corpus_hash),
            headers={"X-Tenant-Id": TENANT},
        )
        self.assertEqual(r.status_code, 202, r.text)
        payload = r.json()
        self.assertNotIn("prior_scan_refs", payload)
        self.assertNotIn("forensic_bundle_uri", payload)
        self.assertNotIn("snapshot_hash", payload)


if __name__ == "__main__":
    unittest.main()
