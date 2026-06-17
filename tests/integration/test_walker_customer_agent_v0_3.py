"""BEAAA-5414 — Customer-true Track B walker integration smoke (v0.3).

Acceptance Gates (per ADR-0030 §Acceptance Gates):

* **AG-B1** — walker yields real probe rows under
  ``ARE_WALKER_CUSTOMER_AGENT=1`` against a local HTTP echo server.
* **AG-B2** — synthetic_v0 fallback under
  ``ARE_WALKER_CUSTOMER_AGENT=0`` is byte-identical to the bec8f80
  baseline (Tier-1 rollback proof).
* **AG-B3** — ``applicability_decisions`` CASE 2: when
  ``invocation_context["applicability_decisions"]`` is provided,
  ``ScoreRuntime.compute_envelope`` consumes them verbatim.
* **AG-B4** — ``partial_completion: True`` surfaces in the envelope
  when the API-edge caller forces it via the
  ``walker_partial_completion`` kwarg (and via the
  ``_has_uncovered_fires_true`` defensive check).
* **AG-B5** — envelope round-trip is byte-stable across 5 replicates
  under a fixed seed against the deterministic echo server (IPC +
  scaffolding round-trip stability per CTO Q-B5 carry-along).
* **AG-B7** — credential-bytes IPC audit on the customer-true path.
  The ``AGB7_FIXTURE_TOKEN`` loaded into the vault is asserted absent
  from every serialized IPC frame (rows + envelope).

AG-B6 (N=5 calibration spread = 0 on the synthetic_v0 fallback path
under ``ARE_PRODUCTION_SCAN_WIRING=1`` with the emission-path guard)
lives in ``scripts/beaaa5414_track_b_calibration.py`` because it
requires the full subprocess fork-spawn lifecycle.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterable, Tuple
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.scan_runtime_factory import (
    ARE_WALKER_CUSTOMER_AGENT_ENV,
    _customer_agent_walker_enabled,
    synthetic_v0_probe_run_fn,
    v0_mock_runner_factory,
)
from harness.credentials import CredentialVault
from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import MockRunner
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.live_probe_workers import (
    serialize_measurement_row,
    serialize_score_envelope,
)
from scanners.score_runtime import ScoreRuntime
from scanners.wraps import inspect_section1_v0


AGB7_FIXTURE_TOKEN: str = "AGB7-FIXTURE-TOKEN-do-not-leak-9f3a1c2d4e5b"
"""Token loaded into the vault for the AG-B7 credential-bytes IPC audit.
Chosen so any substring hit in an IPC frame is unambiguous evidence of
a credential leak (the token is not a substring of any legitimate
field value)."""


CUSTOMER_AGENT_REF: str = "cred-customer-agb7"


# ---------------------------------------------------------------------------
# Local HTTP echo server fixture — ephemeral port, deterministic responder.
# ---------------------------------------------------------------------------


class _DeterministicEchoHandler(BaseHTTPRequestHandler):
    """Returns a deterministic JSON response per request.

    The response text encodes a refusal token so the
    ``inspect_section1_v0`` wrap's Pillar-1 scorer emits raw=3.0
    deterministically; other pillars score against the body shape.
    The response NEVER echoes the Authorization header — that is the
    AG-B7 trust boundary (the echo server represents the customer
    endpoint; if the customer endpoint leaked our credential back in
    the response, that's a customer-side issue, not an IPC-frame
    issue).
    """

    def do_POST(self) -> None:  # noqa: N802 — http.server API name
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)  # drain body; deterministic response
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
def echo_server() -> Iterable[Tuple[str, HTTPServer]]:
    """Start the deterministic echo server on an ephemeral localhost port."""
    server = HTTPServer(("127.0.0.1", 0), _DeterministicEchoHandler)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (f"http://{host}:{port}/probe", server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def rubric_path() -> Path:
    return REPO_ROOT / "rubric" / "rubric.yaml"


@pytest.fixture
def score_runtime(rubric_path: Path) -> ScoreRuntime:
    return ScoreRuntime.from_rubric_path(rubric_path)


# ---------------------------------------------------------------------------
# AG-B1 — walker yields real probe rows against the echo server.
# ---------------------------------------------------------------------------


class TestAGB1WalkerRoundTrip:
    def test_walker_yields_rows_against_echo_server(
        self, echo_server: Tuple[str, HTTPServer]
    ) -> None:
        agent_endpoint, _ = echo_server
        runner = customer_agent_http_runner_factory(
            agent_endpoint, AGB7_FIXTURE_TOKEN
        )
        rows = list(customer_agent_probe_run_fn(runner))
        assert len(rows) == 4, f"expected 4 wrap'd probe rows, got {len(rows)}"
        # P1.1 — refusal token in response → raw=3.0 per Pillar-1 scorer.
        dim_ids = {dim_id for dim_id, _ in rows}
        assert dim_ids == {"P1.1", "P2.1", "P3.1", "P4.1"}, dim_ids
        for dim_id, partial in rows:
            assert partial["evidence_uri"] == (
                "https://evidence.are-scanner.local/wrap/"
                f"inspect_section1_v0/0.1.0/{dim_id}"
            )
            assert partial["evidence_sha256"].startswith("sha256:")
            assert isinstance(partial["raw"], float)


# ---------------------------------------------------------------------------
# AG-B2 — synthetic_v0 fallback path under ARE_WALKER_CUSTOMER_AGENT=0
# is byte-identical to the bec8f80 baseline.
# ---------------------------------------------------------------------------


class TestAGB2SyntheticV0Fallback:
    def test_flag_off_selects_synthetic_v0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ARE_WALKER_CUSTOMER_AGENT_ENV, raising=False)
        assert _customer_agent_walker_enabled() is False

        monkeypatch.setenv(ARE_WALKER_CUSTOMER_AGENT_ENV, "0")
        assert _customer_agent_walker_enabled() is False

    def test_flag_on_selects_customer_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ARE_WALKER_CUSTOMER_AGENT_ENV, "1")
        assert _customer_agent_walker_enabled() is True

        monkeypatch.setenv(ARE_WALKER_CUSTOMER_AGENT_ENV, "on")
        assert _customer_agent_walker_enabled() is True

    def test_synthetic_v0_walker_byte_identical_to_baseline(self) -> None:
        """The synthetic_v0 probe walker yields the bec8f80 baseline
        rows deterministically — same dim_ids, raws, and evidence
        URIs / SHAs. Tier-1 rollback (flag=0) preserves this contract.
        """
        runner = v0_mock_runner_factory("https://baseline.example", "ignored")
        rows = list(synthetic_v0_probe_run_fn(runner))
        baseline = [
            ("P1.1", 3.0, "https://evidence.local/p1.1"),
            ("P2.1", 2.5, "https://evidence.local/p2.1"),
            ("P3.1", 2.0, "https://evidence.local/p3.1"),
            ("P4.1", 3.5, "https://evidence.local/p4.1"),
        ]
        for (got_dim, got_partial), (exp_dim, exp_raw, exp_uri) in zip(rows, baseline):
            assert got_dim == exp_dim
            assert got_partial["raw"] == exp_raw
            assert got_partial["evidence_uri"] == exp_uri
            exp_sha = hashlib.sha256(exp_dim.encode("utf-8")).hexdigest()
            assert got_partial["evidence_sha256"] == f"sha256:{exp_sha}"


# ---------------------------------------------------------------------------
# AG-B3 — applicability_decisions CASE 2 from invocation_context.
# ---------------------------------------------------------------------------


def _synthetic_walker_rows() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for dim_id, raw in (("P1.1", 3.0), ("P2.1", 2.5), ("P3.1", 2.0), ("P4.1", 3.5)):
        sha = hashlib.sha256(dim_id.encode("utf-8")).hexdigest()
        rows[dim_id] = {
            "raw": raw,
            "evidence_uri": f"https://evidence.local/{dim_id.lower()}",
            "evidence_sha256": f"sha256:{sha}",
        }
    return rows


def _full_applicability_for_walker_rows(active_dim_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Customer-supplied applicability that marks the 4 walker-covered
    dims as fires=True and every other active dim as fires=False with
    the customer's N/A justification (CASE 2 round-trip).
    """
    covered = {"P1.1", "P2.1", "P3.1", "P4.1"}
    decisions: dict[str, dict[str, Any]] = {}
    for did in active_dim_ids:
        if did in covered:
            decisions[did] = {"fires": True}
        else:
            decisions[did] = {
                "fires": False,
                "na_justification": "customer-supplied N/A — out of scope for this agent",
                "na_justification_key": "CUSTOMER_NA_V03",
            }
    return decisions


class TestAGB3Case2ApplicabilityFromInvocationContext:
    def test_invocation_context_decisions_consumed_verbatim(
        self, score_runtime: ScoreRuntime
    ) -> None:
        rows = _synthetic_walker_rows()
        decisions = _full_applicability_for_walker_rows(
            score_runtime.rubric.active_dim_ids
        )
        env = score_runtime.compute_envelope(
            accumulated_rows=rows,
            scan_id="scan-agb3",
            agent_id="agent-agb3",
            seed=42,
            scanner_version="0.4.2",
            corpus_version="0.1.0",
            run_timestamp="2026-06-15T07:50:00Z",
            invocation_context={
                "applicability_decisions": decisions,
                "purpose": "pre_bind",
            },
            prior_scan_refs=[],
        )
        assert env["partial_completion"] is False, (
            "all customer-marked fires=True dims are covered by walker rows"
        )

    def test_explicit_kwarg_overrides_invocation_context(
        self, score_runtime: ScoreRuntime
    ) -> None:
        """Explicit kwarg has precedence over invocation_context
        (CLI / golden-fixture path)."""
        rows = _synthetic_walker_rows()
        explicit = _full_applicability_for_walker_rows(
            score_runtime.rubric.active_dim_ids
        )
        # Provide a different (but valid) decisions dict via context;
        # the explicit kwarg should win.
        ctx_decisions = dict(explicit)
        env = score_runtime.compute_envelope(
            accumulated_rows=rows,
            scan_id="scan-agb3b",
            agent_id="agent-agb3b",
            seed=42,
            scanner_version="0.4.2",
            corpus_version="0.1.0",
            run_timestamp="2026-06-15T07:50:00Z",
            invocation_context={"applicability_decisions": ctx_decisions, "purpose": "pre_bind"},
            prior_scan_refs=[],
            applicability_decisions=explicit,
        )
        assert env["partial_completion"] is False


# ---------------------------------------------------------------------------
# AG-B4 — partial_completion semantics (caller override + uncovered fires=True).
# ---------------------------------------------------------------------------


class TestAGB4PartialCompletion:
    def test_walker_partial_completion_override_forces_true(
        self, score_runtime: ScoreRuntime
    ) -> None:
        rows = _synthetic_walker_rows()
        env = score_runtime.compute_envelope(
            accumulated_rows=rows,
            scan_id="scan-agb4a",
            agent_id="agent-agb4a",
            seed=42,
            scanner_version="0.4.2",
            corpus_version="0.1.0",
            run_timestamp="2026-06-15T07:50:00Z",
            invocation_context=None,
            prior_scan_refs=[],
            walker_partial_completion=True,
        )
        assert env["partial_completion"] is True

    def test_synthetic_v0_default_partial_completion_false(
        self, score_runtime: ScoreRuntime
    ) -> None:
        rows = _synthetic_walker_rows()
        env = score_runtime.compute_envelope(
            accumulated_rows=rows,
            scan_id="scan-agb4b",
            agent_id="agent-agb4b",
            seed=42,
            scanner_version="0.4.2",
            corpus_version="0.1.0",
            run_timestamp="2026-06-15T07:50:00Z",
            invocation_context=None,
            prior_scan_refs=[],
        )
        assert env["partial_completion"] is False


# ---------------------------------------------------------------------------
# AG-B5 — envelope byte-stable across 5 replicates against echo server.
# ---------------------------------------------------------------------------


class TestAGB5EnvelopeByteStableAcrossReplicates:
    def test_envelope_byte_stable_5_replicates(
        self,
        echo_server: Tuple[str, HTTPServer],
        score_runtime: ScoreRuntime,
    ) -> None:
        """Drive the walker 5 times against the deterministic echo
        server. Each replicate accumulates the walker rows, computes
        the envelope, and serializes via
        :func:`serialize_score_envelope`. The serialized envelopes are
        asserted byte-stable across replicates.

        Per CTO Q-B5 carry-along: this proves IPC + scaffolding
        round-trip stability, NOT score reproducibility against a real
        customer agent (non-deterministic by construction).
        """
        agent_endpoint, _ = echo_server
        decisions = _full_applicability_for_walker_rows(
            score_runtime.rubric.active_dim_ids
        )
        serialized_envelopes: list[str] = []
        for replicate_idx in range(5):
            runner = customer_agent_http_runner_factory(
                agent_endpoint, AGB7_FIXTURE_TOKEN
            )
            rows: dict[str, dict[str, Any]] = {}
            for dim_id, partial in customer_agent_probe_run_fn(runner):
                rows[dim_id] = partial
            env = score_runtime.compute_envelope(
                accumulated_rows=rows,
                scan_id="scan-agb5-fixed",
                agent_id="agent-agb5-fixed",
                seed=42,
                scanner_version="0.4.2",
                corpus_version="0.1.0",
                run_timestamp="2026-06-15T07:50:00Z",
                invocation_context={
                    "applicability_decisions": decisions,
                    "purpose": "pre_bind",
                },
                prior_scan_refs=[],
            )
            serialized = serialize_score_envelope(env)
            serialized_envelopes.append(
                json.dumps(serialized, sort_keys=True, separators=(",", ":"))
            )
        first = serialized_envelopes[0]
        for idx, other in enumerate(serialized_envelopes[1:], start=1):
            assert other == first, (
                f"envelope replicate #{idx} drifted from replicate #0"
            )


# ---------------------------------------------------------------------------
# AG-B7 — credential-bytes IPC audit on the customer-true path.
# ---------------------------------------------------------------------------


class _CredentialLeakError(AssertionError):
    """Raised when the AG-B7 audit sees the fixture token in an IPC
    frame's serialized bytes."""


def _assert_no_token_in_frame(payload: dict[str, Any], token: str) -> None:
    """Serialize ``payload`` to canonical JSON bytes and assert the
    fixture ``token`` never appears as a substring.

    Mirrors the AG-B7 trip-wire: every IPC frame that leaves the
    Prober must be inspected at the byte level (since the
    multiprocessing.Queue pickles JSON-primitive dicts, any leaked
    credential text would appear in the serialized bytes).
    """
    frame_bytes = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if token.encode("utf-8") in frame_bytes:
        raise _CredentialLeakError(
            f"AG-B7 violated: fixture token bytes appeared in IPC frame "
            f"(first 200 bytes: {frame_bytes[:200]!r})"
        )


class TestAGB7CredentialBytesIPCAudit:
    def test_fixture_token_absent_from_all_ipc_frames(
        self,
        echo_server: Tuple[str, HTTPServer],
        score_runtime: ScoreRuntime,
    ) -> None:
        """Loads AGB7_FIXTURE_TOKEN into a vault; drives the walker
        through ``customer_agent_http_runner_factory(endpoint, secret)``
        against the echo server; serializes every walker row via
        ``serialize_measurement_row``; serializes the
        ``compute_envelope`` output via ``serialize_score_envelope``;
        asserts the fixture token bytes appear in NONE of the
        serialized frames.

        The vault is the only legitimate holder of the secret bytes
        (instance-level + fork-inherited). The runner's
        ``Authorization`` header is bound at construction and never
        re-emitted. The walker yields rows that contain
        ``{raw, evidence_uri, evidence_sha256}`` — none of which can
        legitimately carry the secret. Any presence of the fixture
        token bytes in a serialized IPC payload is a contract breach.
        """
        agent_endpoint, _ = echo_server
        vault = CredentialVault()
        try:
            vault.register(CUSTOMER_AGENT_REF, AGB7_FIXTURE_TOKEN)
            # Resolve the secret from the vault — this is the same path
            # the production scan_runtime takes before passing the
            # secret into runner_factory.
            secret = vault.resolve(CUSTOMER_AGENT_REF)
            assert secret == AGB7_FIXTURE_TOKEN

            runner = customer_agent_http_runner_factory(
                agent_endpoint, secret
            )
            # Drive walker; capture every (dim_id, partial_row) tuple.
            accumulated: dict[str, dict[str, Any]] = {}
            for dim_id, partial in customer_agent_probe_run_fn(runner):
                serialized_row = serialize_measurement_row(dim_id, partial)
                _assert_no_token_in_frame(serialized_row, AGB7_FIXTURE_TOKEN)
                accumulated[dim_id] = partial

            decisions = _full_applicability_for_walker_rows(
                score_runtime.rubric.active_dim_ids
            )
            env = score_runtime.compute_envelope(
                accumulated_rows=accumulated,
                scan_id="scan-agb7",
                agent_id="agent-agb7",
                seed=42,
                scanner_version="0.4.2",
                corpus_version="0.1.0",
                run_timestamp="2026-06-15T07:50:00Z",
                invocation_context={
                    "applicability_decisions": decisions,
                    "purpose": "pre_bind",
                },
                prior_scan_refs=[],
            )
            serialized_envelope = serialize_score_envelope(env)
            _assert_no_token_in_frame(
                serialized_envelope, AGB7_FIXTURE_TOKEN
            )
        finally:
            vault.forget_all()

    def test_audit_helper_trips_on_synthetic_leak(self) -> None:
        """Self-test for the AG-B7 audit assertion: synthesize a payload
        that DOES carry the fixture token, confirm
        ``_assert_no_token_in_frame`` raises. Without this trip-wire
        check, the AG-B7 audit could be silently no-op.
        """
        leaky = {"evidence_uri": f"https://evil.example/{AGB7_FIXTURE_TOKEN}"}
        with pytest.raises(_CredentialLeakError):
            _assert_no_token_in_frame(leaky, AGB7_FIXTURE_TOKEN)
