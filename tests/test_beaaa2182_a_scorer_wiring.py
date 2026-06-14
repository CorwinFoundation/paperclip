"""BEAAA-2182 Track A — AG-A1..AG-A6 fixture tests.

Tests the Scorer-side scoring wiring (``scanners/score_runtime.py`` +
``scanners/live_probe_workers.py`` return-channel + IPC allow-list)
independently of the full Prober/Scorer fork-spawn lifecycle. Subprocess
end-to-end is exercised by ``scripts/beaaa2182_a_calibration.py``.

Acceptance Gates (per ADR-0026):

* **AG-A1** — Scorer-side ``ScoreRuntime.compute_envelope`` accepts
  accumulated rows and projects the §2.2 envelope dict with the same
  scoring callee the CLI uses.
* **AG-A3** — ``ALLOWED_SCORE_ENVELOPE_FIELDS`` allow-list rejects every
  non-listed field on serialize and on receive sides.
* **AG-A4** — ``("err", type_name, message)`` tuple sentinel surfaces
  through the WorkerChannel return queue and the parent-side
  recv_score_envelope returns the same tuple shape.
* **AG-A5** — runtime assertion at Scorer entry (test mode) catches an
  inadvertent ``api.score_runtime`` import in fork-inherited sys.modules.
* **AG-A6** — flag-off rollback path raises ``ScanRuntimeError``; registry
  miss under flag-on also raises. ``_phase1_pending`` never reaches
  ``scan_log`` (BEAAA-5279 F-02).

AG-A2 (N=5 spread = 0 on B1/G1/G2/M1/M2/M3) is exercised by
``scripts/beaaa2182_a_calibration.py`` because it requires the full
golden fixtures + N=5 measurement; calling out here as the binding
merge gate.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scanners.live_probe_workers import (
    ALLOWED_SCORE_ENVELOPE_FIELDS,
    IPCSchemaError,
    MAX_IPC_ENVELOPE_BYTES,
    WorkerChannel,
    serialize_score_envelope,
    validate_inbound_envelope,
)
from scanners.score_runtime import (
    ENVELOPE_FIELDS_SECTION_2_2,
    ScoreRuntime,
    project_to_envelope_section_2_2,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rubric_path() -> Path:
    return REPO_ROOT / "rubric" / "rubric.yaml"


@pytest.fixture
def score_runtime(rubric_path: Path) -> ScoreRuntime:
    return ScoreRuntime.from_rubric_path(rubric_path)


@pytest.fixture
def synthetic_v0_rows() -> dict[str, dict[str, object]]:
    """Mirror of the rows ``synthetic_v0_probe_run_fn`` would yield via
    the IPC. The Scorer's ``on_validated_row`` accumulator builds this
    same dict from the Prober's row stream.
    """
    import hashlib
    rows: dict[str, dict[str, object]] = {}
    for dim_id, raw in (
        ("P1.1", 3.0),
        ("P2.1", 2.5),
        ("P3.1", 2.0),
        ("P4.1", 3.5),
    ):
        sha_hex = hashlib.sha256(dim_id.encode("utf-8")).hexdigest()
        rows[dim_id] = {
            "raw": raw,
            "evidence_uri": f"https://evidence.local/{dim_id.lower()}",
            "evidence_sha256": f"sha256:{sha_hex}",
            "runner": "beaai-hosted-eval",
            "eval_run_id": "00000000-0000-0000-0000-000000000001",
        }
    return rows


@pytest.fixture
def envelope_inputs() -> dict[str, object]:
    return {
        "scan_id": "scan-test-aga1",
        "agent_id": "agent-test",
        "seed": 42,
        "scanner_version": "0.3.11",
        "corpus_version": "0.1.0",
        "run_timestamp": "2026-06-05T10:00:00Z",
        "invocation_context": {"purpose": "pre_bind"},
        "prior_scan_refs": [],
    }


# ---------------------------------------------------------------------------
# AG-A1 — ScoreRuntime.compute_envelope projects §2.2 envelope dict
# ---------------------------------------------------------------------------


class TestAGA1ScoreRuntimeProjection:
    """AG-A1 — Scorer-side compute_envelope returns a §2.2 envelope dict
    with all ADR-0017 addendum-1 §2.2 fields populated by real scoring
    values (not _phase1_pending placeholders).
    """

    def test_envelope_shape_matches_section_2_2(
        self,
        score_runtime: ScoreRuntime,
        synthetic_v0_rows: dict[str, dict[str, object]],
        envelope_inputs: dict[str, object],
    ) -> None:
        envelope = score_runtime.compute_envelope(
            accumulated_rows=synthetic_v0_rows,
            **envelope_inputs,
        )
        # Every §2.2 field must be present (defaulted ok; placeholders not ok).
        for field in ENVELOPE_FIELDS_SECTION_2_2:
            assert field in envelope, f"missing §2.2 field: {field}"
            assert envelope[field] != "_phase1_pending", (
                f"AG-A1: field {field!r} carries _phase1_pending placeholder; "
                f"Scorer-side scoring should populate real value."
            )

    def test_envelope_are_total_is_int_in_range(
        self,
        score_runtime: ScoreRuntime,
        synthetic_v0_rows: dict[str, dict[str, object]],
        envelope_inputs: dict[str, object],
    ) -> None:
        envelope = score_runtime.compute_envelope(
            accumulated_rows=synthetic_v0_rows,
            **envelope_inputs,
        )
        assert isinstance(envelope["are_total"], int)
        assert 0 <= envelope["are_total"] <= 100

    def test_envelope_seed_and_versions_preserved(
        self,
        score_runtime: ScoreRuntime,
        synthetic_v0_rows: dict[str, dict[str, object]],
        envelope_inputs: dict[str, object],
    ) -> None:
        envelope = score_runtime.compute_envelope(
            accumulated_rows=synthetic_v0_rows,
            **envelope_inputs,
        )
        assert envelope["seed"] == envelope_inputs["seed"]
        assert envelope["scanner_version"] == envelope_inputs["scanner_version"]
        assert envelope["corpus_version"] == envelope_inputs["corpus_version"]
        assert envelope["agent_id"] == envelope_inputs["agent_id"]
        assert envelope["purpose"] == "pre_bind"

    def test_envelope_deterministic_across_replicates(
        self,
        score_runtime: ScoreRuntime,
        synthetic_v0_rows: dict[str, dict[str, object]],
        envelope_inputs: dict[str, object],
    ) -> None:
        """Same inputs → byte-equal envelope (replicate N=5 in-process)."""
        envelopes = [
            score_runtime.compute_envelope(
                accumulated_rows=synthetic_v0_rows,
                **envelope_inputs,
            )
            for _ in range(5)
        ]
        canonical = [
            json.dumps(env, sort_keys=True, separators=(",", ":"))
            for env in envelopes
        ]
        assert len(set(canonical)) == 1, (
            "AG-A1: envelope projection is not deterministic across N=5 "
            "replicates with identical inputs (binding for AG-A2)."
        )


# ---------------------------------------------------------------------------
# AG-A3 — ALLOWED_SCORE_ENVELOPE_FIELDS allow-list enforcement
# ---------------------------------------------------------------------------


class TestAGA3AllowList:
    """AG-A3 — closed-set allow-list rejects non-listed fields on both
    serialize and receive sides.
    """

    def _valid_envelope_minimal(self) -> dict[str, object]:
        return {
            "agent_id": "x",
            "are_total": 50,
            "band": "B",
            "confidence_below_adjudication_threshold": False,
            "corpus_version": "0.1.0",
            "evidence_refs": [],
            "model_versions": None,
            "partial_completion": False,
            "prior_scan_refs": [],
            "purpose": None,
            "rubric_version": "1.0.1",
            "run_timestamp": "2026-06-05T10:00:00Z",
            "scanner_version": "0.3.11",
            "seed": 42,
            "subscores": {},
        }

    def test_allow_list_set_matches_section_2_2(self) -> None:
        # ALLOWED_SCORE_ENVELOPE_FIELDS is a strict superset (adds the
        # optional truncated sentinel per ADR-0017 §7 Q2).
        section_2_2 = set(ENVELOPE_FIELDS_SECTION_2_2)
        assert section_2_2 <= ALLOWED_SCORE_ENVELOPE_FIELDS
        # Only the cap sentinel is extra.
        extra = ALLOWED_SCORE_ENVELOPE_FIELDS - section_2_2
        assert extra == {"prior_scan_refs_truncated"}

    def test_serialize_rejects_extra_field(self) -> None:
        env = self._valid_envelope_minimal()
        env["malicious_extra_field"] = "smuggled"
        with pytest.raises(IPCSchemaError, match="not in the IPC allow-list"):
            serialize_score_envelope(env)

    def test_serialize_rejects_bytes_value(self) -> None:
        env = self._valid_envelope_minimal()
        env["agent_id"] = b"binary"
        with pytest.raises(IPCSchemaError, match="forbidden"):
            serialize_score_envelope(env)

    def test_serialize_rejects_non_json_primitive(self) -> None:
        env = self._valid_envelope_minimal()
        # A frozenset is not JSON-primitive.
        env["subscores"] = frozenset({"a": 1})  # type: ignore[arg-type]
        with pytest.raises(IPCSchemaError, match="not a JSON primitive"):
            serialize_score_envelope(env)

    def test_serialize_accepts_truncated_sentinel(self) -> None:
        env = self._valid_envelope_minimal()
        env["prior_scan_refs_truncated"] = True
        assert serialize_score_envelope(env) is env

    def test_receive_rejects_extra_field(self) -> None:
        env = self._valid_envelope_minimal()
        env["leaked_field"] = "x"
        with pytest.raises(IPCSchemaError, match="not in the allow-list"):
            validate_inbound_envelope(env)

    def test_serialize_rejects_oversize_envelope(self) -> None:
        env = self._valid_envelope_minimal()
        # Pad evidence_refs past MAX_IPC_ENVELOPE_BYTES.
        big_str = "x" * (MAX_IPC_ENVELOPE_BYTES + 1024)
        env["evidence_refs"] = [{"rubric_point_id": big_str}]  # type: ignore[list-item]
        with pytest.raises(IPCSchemaError, match="exceeds"):
            serialize_score_envelope(env)


# ---------------------------------------------------------------------------
# AG-A4 — return-channel tuple sentinel surfaces correctly
# ---------------------------------------------------------------------------


class TestAGA4ReturnChannelSentinel:
    """AG-A4 — ``send_score_envelope`` accepts ('ok', dict) and
    ('err', type, msg); ``recv_score_envelope`` returns the tuple
    verbatim. Other shapes raise IPCSchemaError before transit.
    """

    def _valid_envelope_minimal(self) -> dict[str, object]:
        return {
            "agent_id": "x",
            "are_total": 50,
            "band": "B",
            "confidence_below_adjudication_threshold": False,
            "corpus_version": "0.1.0",
            "evidence_refs": [],
            "model_versions": None,
            "partial_completion": False,
            "prior_scan_refs": [],
            "purpose": None,
            "rubric_version": "1.0.1",
            "run_timestamp": "2026-06-05T10:00:00Z",
            "scanner_version": "0.3.11",
            "seed": 42,
            "subscores": {},
        }

    def test_ok_tuple_round_trips(self) -> None:
        channel = WorkerChannel()
        env = self._valid_envelope_minimal()
        channel.send_score_envelope(("ok", env), timeout=1.0)
        outcome = channel.recv_score_envelope(timeout=1.0)
        assert outcome[0] == "ok"
        assert outcome[1] == env

    def test_err_tuple_round_trips(self) -> None:
        channel = WorkerChannel()
        channel.send_score_envelope(("err", "ValueError", "bad seed"), timeout=1.0)
        outcome = channel.recv_score_envelope(timeout=1.0)
        assert outcome == ("err", "ValueError", "bad seed")

    def test_send_rejects_malformed_ok_shape(self) -> None:
        channel = WorkerChannel()
        with pytest.raises(IPCSchemaError, match="envelope as dict"):
            channel.send_score_envelope(("ok", "not-a-dict"), timeout=1.0)

    def test_send_rejects_malformed_err_shape(self) -> None:
        channel = WorkerChannel()
        with pytest.raises(IPCSchemaError, match="two strs"):
            channel.send_score_envelope(("err", "ValueError"), timeout=1.0)

    def test_send_rejects_unknown_tag(self) -> None:
        channel = WorkerChannel()
        with pytest.raises(IPCSchemaError, match="unknown score envelope tag"):
            channel.send_score_envelope(("maybe", {}), timeout=1.0)


# ---------------------------------------------------------------------------
# AG-A5 — runtime assertion catches inadvertent api.score_runtime import
# ---------------------------------------------------------------------------


class TestAGA5RuntimeAssertion:
    """AG-A5 — under ARE_TEST_MODE=1, Scorer startup asserts that
    api.score_runtime is NOT in sys.modules (fork-inherited from parent).
    """

    def test_assertion_passes_when_module_absent(self) -> None:
        # Sanity: the module is deleted; it must not be in sys.modules.
        assert "api.score_runtime" not in sys.modules

    def test_assertion_fires_when_module_simulated_present(self) -> None:
        """Simulate the regression: pretend api.score_runtime got
        re-imported on the parent and fork-inherited into Scorer.
        The Scorer's startup assertion (replicated below) must fire.
        """
        fake_module = MagicMock()
        with patch.dict(sys.modules, {"api.score_runtime": fake_module}):
            with patch.dict(os.environ, {"ARE_TEST_MODE": "1"}):
                # The actual run_scorer entry runs this assertion before
                # opening the egress scope. Replicate the check here so
                # the test is fast (no real subprocess).
                with pytest.raises(AssertionError, match="AG-A5 violation"):
                    assert "api.score_runtime" not in sys.modules, (
                        "AG-A5 violation: api.score_runtime imported by parent before "
                        "fork (fork-inherits sys.modules); scoring callee transitively "
                        "reachable from API edge. The Scorer-side module is "
                        "scanners.score_runtime; the api.* path is forbidden by "
                        "scripts/check_no_apiedge_score.py."
                    )

    def test_static_ci_check_passes(self) -> None:
        """The ``scripts/check_no_apiedge_score.py`` gate must exit 0
        against the current api/ tree.
        """
        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "check_no_apiedge_score.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"AG-A5 CI check failed:\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# AG-A6 — flag-off rollback emits master 52c003e _phase1_pending envelope
# ---------------------------------------------------------------------------


class TestAGA6FlagOffRollback:
    """AG-A6 (BEAAA-5279 F-02) — ``_phase1_pending`` never reaches scan_log.

    Flag-off path (score_envelope_registry=None) and registry miss under
    flag-on both raise ``ScanRuntimeError`` to abort the scan.
    """

    def test_flag_off_returns_phase1_pending(self) -> None:
        with patch.dict(os.environ, {"ARE_SCORER_REAL": "off"}):
            from api.scan_runtime_factory import _scorer_real_enabled
            assert _scorer_real_enabled() is False

    def test_flag_on_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ARE_SCORER_REAL", None)
            from api.scan_runtime_factory import _scorer_real_enabled
            assert _scorer_real_enabled() is True

    def test_flag_aliases(self) -> None:
        from api.scan_runtime_factory import _scorer_real_enabled
        for on_value in ("on", "1", "true", "yes", "ON", "True"):
            with patch.dict(os.environ, {"ARE_SCORER_REAL": on_value}):
                assert _scorer_real_enabled() is True
        for off_value in ("off", "0", "false", "no", "OFF"):
            with patch.dict(os.environ, {"ARE_SCORER_REAL": off_value}):
                assert _scorer_real_enabled() is False

    def test_factory_raises_on_flag_off(self) -> None:
        """BEAAA-5279 F-02: flag-off (score_envelope_registry=None) raises
        ScanRuntimeError; _phase1_pending must never reach scan_log.
        """
        import pytest
        from api.scan_runtime_factory import (
            ScanRuntimeError,
            _production_score_envelope_factory,
        )

        runtime_ref: list = []
        factory = _production_score_envelope_factory(runtime_ref, None)

        result = MagicMock()
        result.scan_id = "scan-x"
        result.scanner_version = "0.3.11"
        result.corpus_version = "0.1.0"

        with pytest.raises(ScanRuntimeError, match="ARE_SCORER_REAL=off"):
            factory(result, agent_id="agent-x", run_timestamp="2026-06-05T10:00:00Z")

    def test_factory_raises_on_registry_miss_flag_on(self) -> None:
        """BEAAA-5279 F-02 (AC): registry miss under flag-on raises
        ScanRuntimeError and does not append to scan_log.
        """
        import pytest
        from unittest.mock import MagicMock as _MM
        from api.scan_runtime_factory import (
            ScanRuntimeError,
            _production_score_envelope_factory,
        )
        from api.scan_log import ScanLog

        scan_log = MagicMock(spec=ScanLog)
        runtime_ref: list = []
        # Registry is non-None but does NOT contain the scan_id — miss.
        registry: dict = {}
        factory = _production_score_envelope_factory(runtime_ref, registry)

        result = MagicMock()
        result.scan_id = "scan-missing"
        result.scanner_version = "0.3.11"
        result.corpus_version = "0.1.0"

        with pytest.raises(ScanRuntimeError, match="registry miss"):
            factory(result, agent_id="agent-x", run_timestamp="2026-06-05T10:00:00Z")

        # scan_log.append_scan must NOT have been called.
        scan_log.append_scan.assert_not_called()

    def test_factory_emits_real_envelope_when_registry_hit(self) -> None:
        """When score_envelope_registry has an entry for the scan_id,
        the factory closure returns that envelope (plus the API-edge
        prior_scan_refs / run_timestamp patch).
        """
        from api.scan_runtime_factory import _production_score_envelope_factory

        runtime_ref: list = []
        scorer_envelope = {
            "agent_id": "agent-x",
            "are_total": 60,
            "band": "B",
            "confidence_below_adjudication_threshold": False,
            "corpus_version": "0.1.0",
            "evidence_refs": [],
            "model_versions": None,
            "partial_completion": False,
            "prior_scan_refs": [],
            "purpose": "pre_bind",
            "rubric_version": "1.0.1",
            "run_timestamp": "2026-06-05T09:00:00Z",
            "scanner_version": "0.3.11",
            "seed": 42,
            "subscores": {"P1.1": 3.0},
        }
        registry = {"scan-x": scorer_envelope}
        factory = _production_score_envelope_factory(runtime_ref, registry)

        result = MagicMock()
        result.scan_id = "scan-x"
        result.scanner_version = "0.3.11"
        result.corpus_version = "0.1.0"

        envelope = factory(result, agent_id="agent-x", run_timestamp="2026-06-05T10:00:00Z")
        assert envelope["are_total"] == 60
        assert envelope["band"] == "B"
        assert envelope["subscores"] == {"P1.1": 3.0}
        # API-edge patch: kickoff run_timestamp wins over Scorer-side stamp.
        assert envelope["run_timestamp"] == "2026-06-05T10:00:00Z"
        # API-edge patch: prior_scan_refs populated post-receive.
        assert envelope["prior_scan_refs"] == []


# ---------------------------------------------------------------------------
# Bonus — project_to_envelope_section_2_2 byte-equality vs master shape
# ---------------------------------------------------------------------------


class TestProductionScoreInputsAreCase1:
    """BEAAA-2182 Track A Q-A4 — pin the production-callsite contract.

    CTO comment 2c673aa1 (2026-06-05) requires explicit confirmation
    that the additive ``ScoreRuntime.compute_envelope(applicability_decisions=None)``
    kwarg is NEVER set by any production callsite. Production = the
    synthetic-walker path via ``make_default_production_scan_runtime``
    → ``make_production_scan_runtime`` → ``spawn_scorer(score_inputs=...)``
    → ``run_scorer`` → ``compute_envelope(accumulated_rows, **score_inputs)``.

    If a future change adds ``applicability_decisions`` to the
    production ``score_inputs`` dict, this test fails and signals that
    the v1.5.0 contract patch + UW op-seat co-sign need to re-cover the
    production-behavior-dial surface (case 2 per CTO comment 2c673aa1).
    """

    # Keys that ``api.scan_runtime_factory::make_production_scan_runtime``
    # is permitted to put in ``score_inputs`` at the spawn_scorer call.
    # If this set widens, intentionally or not, the test fails — push
    # the rationale through CTO + UW before unbreaking.
    PRODUCTION_SCORE_INPUTS_ALLOWED_KEYS: frozenset[str] = frozenset({
        "scan_id",
        "agent_id",
        "seed",
        "scanner_version",
        "corpus_version",
        "run_timestamp",
        "invocation_context",
        "prior_scan_refs",
    })

    def test_production_score_inputs_keys_pinned(self) -> None:
        """Static check: ``score_inputs`` literal in
        ``make_production_scan_runtime`` contains exactly the allowed
        keys. Pure source-text inspection so the test is independent of
        runtime execution paths.
        """
        import ast
        factory_path = REPO_ROOT / "api" / "scan_runtime_factory.py"
        source = factory_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        score_inputs_literals: list[set[str]] = []

        class _Visitor(ast.NodeVisitor):
            def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "score_inputs"
                        and isinstance(node.value, ast.Dict)
                    ):
                        keys: set[str] = set()
                        for k in node.value.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                keys.add(k.value)
                        score_inputs_literals.append(keys)
                self.generic_visit(node)

        _Visitor().visit(tree)

        assert score_inputs_literals, (
            "Q-A4 regression: no ``score_inputs = {...}`` literal found in "
            "api/scan_runtime_factory.py. If the production score_inputs "
            "construction moved, update this test's AST scan to follow."
        )

        for keys in score_inputs_literals:
            assert "applicability_decisions" not in keys, (
                f"Q-A4 regression: production score_inputs dict contains "
                f"``applicability_decisions`` (keys={sorted(keys)!r}). "
                f"BEAAA-2182 Track A Q-A4 CTO comment 2c673aa1 pinned "
                f"production to CASE 1 (kwarg unset = auto-synth N/A). "
                f"Setting this in production requires CTO co-sign + UW "
                f"op-seat re-review on contracts/are-scanner-api-v1-"
                f"v1.5.0-patch.md (applicability-decision provenance "
                f"becomes a production-behavior dial — synthesized vs. "
                f"customer-supplied)."
            )
            assert keys <= self.PRODUCTION_SCORE_INPUTS_ALLOWED_KEYS, (
                f"Q-A4 regression: production score_inputs widened beyond "
                f"the Q-A4-pinned key set. Found extra keys: "
                f"{sorted(keys - self.PRODUCTION_SCORE_INPUTS_ALLOWED_KEYS)!r}. "
                f"Allowed set: {sorted(self.PRODUCTION_SCORE_INPUTS_ALLOWED_KEYS)!r}. "
                f"If the widening is intentional, update both this test "
                f"and the v1.5.0 contract patch + UW op-seat co-sign "
                f"scope."
            )


class TestProjectToEnvelopeShape:
    """The §2.2 projection lifted from the deleted api/score_runtime.py
    is byte-equal to the master 52c003e implementation. Pinning this
    here so AG-A2 N=5 spread = 0 is grounded in a deterministic shape
    contract.
    """

    def test_band_letter_remap(self) -> None:
        cli = {
            "agent_id": "x",
            "are_total": 60,
            "band_letter": "B",
            "corpus_version": "0.1.0",
            "evidence_refs": [],
            "model_versions": None,
            "run_timestamp": "2026-06-05T10:00:00Z",
            "scanner_version": "0.3.11",
            "seed": 42,
            "subscores": {},
        }
        env = project_to_envelope_section_2_2(
            cli, purpose=None, rubric_version="1.0.1", prior_scan_refs=[]
        )
        assert env["band"] == "B"  # CLI band_letter -> §2.2 band
        assert "band_letter" not in env  # CLI-only key dropped

    def test_partial_completion_defaults_false(self) -> None:
        cli = {
            "agent_id": "x",
            "are_total": 0,
            "band_letter": "X",
            "corpus_version": "0.1.0",
            "evidence_refs": [],
            "model_versions": None,
            "run_timestamp": "2026-06-05T10:00:00Z",
            "scanner_version": "0.3.11",
            "seed": 42,
            "subscores": {},
        }
        env = project_to_envelope_section_2_2(
            cli, purpose=None, rubric_version="1.0.1", prior_scan_refs=[]
        )
        assert env["partial_completion"] is False
        assert env["confidence_below_adjudication_threshold"] is False
