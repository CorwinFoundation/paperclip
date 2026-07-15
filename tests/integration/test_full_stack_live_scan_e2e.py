"""BEAAA-12022 — unmocked full-stack live-scan E2E integration test.

Parent: BEAAA-12018 (third-party code-review action item #3 — "assess E2E
gap"). CTO assessment: a real but bounded gap exists. BEAAA-12022 and the
board-duplicate BEAAA-12024 (identical title, same parent, same assignee) are
consolidated here: this single test is the deliverable for both; BEAAA-12024
is cancelled as a duplicate of BEAAA-12022.

The gap this closes
-------------------
Component-level live coverage already exists and is good: the real customer
runner vs a fake upstream (``test_customer_runner_*``), the real scorer via
the golden calibration set, and HTTP wiring (``test_app.py`` +
``test_invocation_context_e2e.py``). But the existing "e2e" test
(``test_invocation_context_e2e.py``) monkeypatches BOTH
:func:`api.scan_runtime_factory.spawn_scorer` AND
:func:`api.scan_runtime_factory._prober_target`, so no single test drives the
*unmocked* full stack where the two proven halves meet.

This module drives that seam end-to-end with NOTHING mocked between the HTTP
edge and the assembled score envelope::

    POST /scanner/v1/rescans           (real FastAPI edge via TestClient)
      -> ScanRuntime.kickoff / make_production_scan_runtime.run_scan_fn
      -> real fork-spawn lifecycle:
           real `_prober_target`       (NOT monkeypatched)
           real `spawn_scorer`         (NOT monkeypatched)
      -> real live_probe_orchestrator.run_scan   (real K-replicate fold,
         real wall-clock / probe / token caps)
      -> real `_CustomerAgentHttpRunner`  (real ScopedEgressAllowlist +
         streaming size cap + compression-bomb + redirect gates) driven
         against a controlled in-process fake agent on loopback
      -> real Scorer subprocess -> real ScoreRuntime.compute_envelope
      -> assembled ADR-0017 addendum-1 §2.2 score envelope in the 202 body.

The ONLY test double is the fake agent endpoint itself (an in-process HTTP
server, ``_edge_fake_upstream.py`` style) plus the loopback URL routed into
the otherwise-real customer runner. That substitution is unavoidable: the
edge composes the endpoint as ``https://<host>`` with no port/scheme control
(``api/scan_handlers.py::_kickoff_live_probe_scan``), so a loopback fixture
cannot be reached through the request body. Loopback is permitted for the
test process by ``tests/integration/conftest.py`` — the egress hardening
ships intact in production.

Acceptance (BEAAA-12022)
------------------------
* This module never patches the orchestrator<->scorer seam. The only
  ``monkeypatch.setattr`` in the file targets
  ``live_probe_orchestrator._now`` (the documented wall-clock test seam);
  the scan pipeline itself — including ``spawn_scorer`` and the prober
  target — runs unmocked by construction.
* Golden Δ0 unaffected — test-only addition, additive and revertible.

Rule-execution per ``contracts/scanner-implementation-supervision-v1.md@v1.0``
("test that doesn't touch behaviour"). No scoring math, judge model, rubric,
or contract-output-shape change. Anchored to
``contracts/are-scanner-api-v1.md`` @ v1.9.0.
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterable, Tuple

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover - fastapi is a test dependency
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.scan_log import ScanLog
from api.scan_runtime_factory import (
    _production_score_envelope_factory,
    build_v0_corpus_tarball,
    make_production_scan_runtime,
    v0_corpus_snapshot_hash,
)
from harness.customer_runner import customer_agent_http_runner_factory
from scanners import live_probe_orchestrator
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.live_probe_orchestrator import (
    MAX_WALL_CLOCK_SECONDS,
    ScanRequest,
)
from scanners.score_runtime import ScoreRuntime


REPO_ROOT = Path(__file__).resolve().parents[2]

TENANT_ID = "tenant-12022"
AGENT_ID = "agent-12022"
CREDENTIAL_REF = "phase1-probe.v1"
CREDENTIAL_SECRET = "secret-12022"
# The edge composes ``https://<host>``; the real customer runner is routed to
# the reachable loopback fixture by the runner factory below. The host is a
# syntactically-valid hostname so ``_validate_agent_endpoint`` passes.
AGENT_HOST = "fixture-agent.local"
# Pinned server-actual provenance for the v0 corpus (manifest corpus_version
# with the seed-42 default the synthetic corpus is built under).
SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "0.1.0"
SEED = 42
K_REPLICATES = 5

# §2.2 envelope provenance keys that must always be present (ADR-0017
# addendum-1). ``are_total`` is the canonical name for what the ticket calls
# ``ari_total`` (ARI is a deprecated term; the contract surface is ARE).
_REQUIRED_ENVELOPE_KEYS = frozenset(
    {
        "agent_id",
        "are_total",
        "band",
        "confidence_below_adjudication_threshold",
        "corpus_version",
        "evidence_refs",
        "model_versions",
        "partial_completion",
        "prior_scan_refs",
        "purpose",
        "rubric_version",
        "run_timestamp",
        "scanner_version",
        "seed",
        "subscores",
    }
)


# ---------------------------------------------------------------------------
# Controlled in-process fake agent endpoint (`_edge_fake_upstream.py` style).
# Deterministic refusal responder: the ``inspect_section1_v0`` wrap scores the
# refusal token to a stable per-dim raw, so the whole stack is reproducible
# for a fixed seed.
# ---------------------------------------------------------------------------


class _DeterministicAgentHandler(BaseHTTPRequestHandler):
    """Answers every probe POST with the same JSON body.

    Wire shape mirrors the real customer-agent contract consumed by
    :class:`harness.customer_runner._CustomerAgentHttpRunner`: request body
    ``{"prompt": ...}``, response body ``{"response": <str>}``. The response
    carries a refusal token so the P1.4 out-of-scope-refusal scorer emits a
    deterministic raw.
    """

    def do_POST(self) -> None:  # noqa: N802 - http.server API name
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)  # drain body; response is deterministic
        body = json.dumps(
            {"response": "I cannot fulfill that request — refusing"}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        return  # silence test stdout


@pytest.fixture(scope="module")
def fake_agent() -> Iterable[Tuple[str, HTTPServer]]:
    """Start the deterministic fake agent on an ephemeral loopback port."""
    server = HTTPServer(("127.0.0.1", 0), _DeterministicAgentHandler)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (f"http://{host}:{port}/probe", server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Full-stack production runtime wiring.
#
# This composes the SAME production callables that
# ``make_default_production_scan_runtime`` wires under
# ``ARE_WALKER_CUSTOMER_AGENT=1`` (real customer walker, real HTTP runner,
# real ScoreRuntime, real scan_log + envelope factory). The only difference
# is the runner-factory wrapper below, which routes the real customer runner
# at the reachable loopback fixture instead of the edge-composed
# ``https://<host>`` (unreachable in-process). Nothing is monkeypatched.
# ---------------------------------------------------------------------------


def _build_full_stack_runtime(
    *, scan_log_path: Path, corpus_dir: Path, fake_agent_url: str
) -> Tuple[Any, Path]:
    corpus_path = build_v0_corpus_tarball(corpus_dir)
    score_runtime = ScoreRuntime.from_rubric_path(
        REPO_ROOT / "rubric" / "rubric.yaml"
    )
    score_envelope_registry: dict[str, dict[str, Any]] = {}

    def loopback_runner_factory(agent_endpoint: str, secret: str) -> Any:
        # The real ``_CustomerAgentHttpRunner`` (ScopedEgressAllowlist +
        # streaming/compression/redirect gates) verbatim — only the URL is
        # the reachable loopback fixture rather than the edge's https host.
        del agent_endpoint
        return customer_agent_http_runner_factory(fake_agent_url, secret)

    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus_path,
        runner_factory=loopback_runner_factory,
        probe_run_fn=customer_agent_probe_run_fn,
        score_runtime=score_runtime,
        score_envelope_registry=score_envelope_registry,
        fired_probe_ledger=None,
    )
    # Mirror make_default_production_scan_runtime's persistence post-set so the
    # /rescans 202 body is the §2.2 envelope (scan_log + envelope factory).
    runtime._scan_log = ScanLog(str(scan_log_path))
    runtime._score_envelope_factory = _production_score_envelope_factory(
        [runtime], score_envelope_registry
    )
    return runtime, corpus_path


def _rescan_body(corpus_hash: str) -> dict[str, Any]:
    return {
        "tenant_id": TENANT_ID,
        "agent_id": AGENT_ID,
        "agent_endpoint": {"host": AGENT_HOST},
        "credential_ref": CREDENTIAL_REF,
        "credential_secret": CREDENTIAL_SECRET,
        "corpus_snapshot_hash": corpus_hash,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "seed": SEED,
        "scan_mode": "live_probe",
        "k_replicates": K_REPLICATES,
        "requested_pillars": [
            "GOVERNANCE",
            "ROBUSTNESS",
            "BIAS_FAIRNESS",
            "OPERATIONS",
        ],
        "scanner_invocation_context": {"purpose": "mid_term"},
    }


def _scan_request(corpus_hash: str, *, scan_id: str) -> ScanRequest:
    return ScanRequest(
        scan_id=scan_id,
        tenant_id=TENANT_ID,
        agent_endpoint=f"https://{AGENT_HOST}",
        credential_ref=CREDENTIAL_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version=SCANNER_VERSION,
        corpus_version=CORPUS_VERSION,
        seed=SEED,
        k_replicates=K_REPLICATES,
        agent_id=AGENT_ID,
        invocation_context={"purpose": "mid_term"},
    )


# ---------------------------------------------------------------------------
# Test 1 — full unmocked path through the HTTP edge; reproducible envelope.
# ---------------------------------------------------------------------------


def test_full_stack_rescan_envelope_reproducible(
    tmp_path: Path, fake_agent: Tuple[str, HTTPServer]
) -> None:
    fake_agent_url, _ = fake_agent
    runtime, corpus_path = _build_full_stack_runtime(
        scan_log_path=tmp_path / "scan_log.sqlite3",
        corpus_dir=tmp_path / "corpus",
        fake_agent_url=fake_agent_url,
    )
    corpus_hash = v0_corpus_snapshot_hash(corpus_path)
    # Satisfy the /rescans "prior scan for agent" 422 guard.
    runtime.register_prior_scan(AGENT_ID)

    client = TestClient(create_app(scan_runtime=runtime, bundles=BundleStore()))

    envelopes: list[dict[str, Any]] = []
    for _ in range(2):
        response = client.post(
            "/scanner/v1/rescans",
            headers={"X-Tenant-Id": TENANT_ID},
            json=_rescan_body(corpus_hash),
        )
        assert response.status_code == 202, response.text
        envelopes.append(response.json())

    env = envelopes[0]

    # (a) Well-formed §2.2 envelope — every required provenance key present.
    missing = _REQUIRED_ENVELOPE_KEYS - env.keys()
    assert not missing, f"envelope missing required keys: {sorted(missing)}"

    # (b) Provenance fields carry the pinned values.
    assert env["scanner_version"] == SCANNER_VERSION
    assert env["corpus_version"] == CORPUS_VERSION
    assert env["seed"] == SEED
    assert env["purpose"] == "mid_term"

    # (c) Real scoring output — are_total is an int, subscores a non-empty map.
    assert isinstance(env["are_total"], int)
    assert isinstance(env["subscores"], dict) and env["subscores"]

    # (d) Scored-coverage state surfaces intact: the walker-covered dims
    # (BEAAA-14425 live minimal set: P1.1, P1.4, ROBUSTNESS.PII.SCAN_TIME_DETECTION)
    # complete against the fake agent, so with auto-synthesized N/A on the
    # rest the scan is not partial. The boolean is present and well-typed
    # regardless of value (`scored:false` coverage surfaces, not swallowed).
    assert isinstance(env["partial_completion"], bool)
    assert env["partial_completion"] is False
    assert env["confidence_below_adjudication_threshold"] is False

    # (e) Persistence pins prove the scan_log write actually ran (§2.2 Path A).
    assert env["forensic_bundle_uri"].startswith("s3://")
    assert isinstance(env["snapshot_hash"], str) and env["snapshot_hash"]

    # (f) Reproducible across two runs at the same seed: same total / subscores
    # within ±1 (the Supreme-Law reproducibility band). The deterministic fake
    # agent -> deterministic raws -> stable envelope; only scan_id /
    # run_timestamp / prior_scan_refs differ between the runs.
    first, second = envelopes
    assert abs(first["are_total"] - second["are_total"]) <= 1
    assert first["subscores"].keys() == second["subscores"].keys()
    for pillar, value in first["subscores"].items():
        assert abs(value - second["subscores"][pillar]) <= 1, pillar


# ---------------------------------------------------------------------------
# Test 2 — real wall-clock cap fires through the real fork-spawn stack.
#
# ``_now`` is the module-level clock indirection the orchestrator documents as
# the test seam for the wall-clock cap; the fork inherits the parent's patched
# value via copy-on-write. Nothing else is mocked — real `_prober_target`,
# real `spawn_scorer`, real customer runner against the fake agent.
# ---------------------------------------------------------------------------


def test_full_stack_wallclock_cap_surfaces_incomplete(
    tmp_path: Path,
    fake_agent: Tuple[str, HTTPServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_url, _ = fake_agent
    runtime, corpus_path = _build_full_stack_runtime(
        scan_log_path=tmp_path / "scan_log.sqlite3",
        corpus_dir=tmp_path / "corpus",
        fake_agent_url=fake_agent_url,
    )
    corpus_hash = v0_corpus_snapshot_hash(corpus_path)

    # Deterministic clock: let the first full replicate complete (1 initial
    # call + one post-row cap check per walker dim, within the <=5 headroom
    # window below) so every dim is covered, then
    # jump past MAX_WALL_CLOCK_SECONDS on the next check. The counter dict is
    # copy-on-write inherited by the forked prober; the parent never calls the
    # orchestrator's ``_now`` (it uses the real join timeout).
    calls = {"n": 0}

    def fake_now() -> float:
        calls["n"] += 1
        if calls["n"] <= 5:
            return 0.0
        return float(MAX_WALL_CLOCK_SECONDS) * 10.0

    monkeypatch.setattr(live_probe_orchestrator, "_now", fake_now)

    scan_id = "scan-12022-wallclock"
    result = runtime.kickoff(
        _scan_request(corpus_hash, scan_id=scan_id),
        CREDENTIAL_SECRET,
        agent_id=AGENT_ID,
        scanner_invocation_context={"purpose": "mid_term"},
    )

    # The real orchestrator halted on the wall-clock ceiling and surfaced an
    # incomplete result — proving the cap is exercised end-to-end, not stubbed.
    assert result.incomplete is True
    assert result.incomplete_reason == "wallclock_cap"
    # At least the first replicate's rows reached the Scorer: the fake agent
    # was genuinely probed before the cap tripped.
    assert result.rows_emitted >= 4

    # The persisted envelope surfaces the degraded state intact (the real
    # Scorer + envelope factory ran on the partial row stream).
    row = runtime.current_kickoff_row(scan_id)
    assert row is not None
    envelope = row["score_json"]
    assert envelope["partial_completion"] is True
    assert envelope["confidence_below_adjudication_threshold"] is True
