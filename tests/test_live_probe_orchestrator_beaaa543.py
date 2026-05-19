"""BEAAA-543 — live-probe orchestrator skeleton.

Covers the acceptance criteria of BEAAA-543 (T2, rule-execution):

* AC#1 — Public surface ``run_scan(scan_request, vault, ipc_outbound) ->
  ScanResult``.
* AC#2 — ``corpus_snapshot_hash`` mismatch raises
  :class:`CorpusSnapshotMismatchError` (T1 translates to HTTP 422).
* AC#3 — Vault lifecycle: ``vault.resolve`` only inside the orchestrator;
  ``vault.forget(credential_ref)`` runs on every exit path including the
  forced-exception path.
* AC#4 — Every emitted row carries provenance ``runner=HOSTED_RUNNER``,
  with the constant imported from :mod:`api.intake_guard` (no literal-
  string drift).
* AC#5 — The IPC row shape matches what T4's
  :func:`scanners.live_probe_workers.validate_inbound` accepts; rows
  round-trip through the T4 serializer + validator without raising.

Out of scope for this test file (covered by sibling tickets):

* BEAAA-544 — K-replicate / median-of-K / variance band.
* BEAAA-545 — probe / token / wall-clock caps.
* BEAAA-547 — ``POST /scan`` route, handler, request validation.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from api.intake_guard import HOSTED_RUNNER
from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners.live_probe_orchestrator import (
    CorpusSnapshotMismatchError,
    CredentialResolveError,
    OrchestratorError,
    ScanRequest,
    ScanResult,
    run_scan,
)
from scanners.live_probe_workers import (
    END_OF_SCAN,
    IPCSchemaError,
    serialize_measurement_row,
    validate_inbound,
)
from scanners.snapshot_hash import compute_corpus_snapshot_hash

SECRET_LITERAL = "SECRET_TEST_DO_NOT_LEAK_a3f9"
CRED_REF = "phase1-probe.v1"
EVAL_RUN_ID_FIXTURE = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# Fakes — keep tests fully synchronous, no multiprocessing.
# ---------------------------------------------------------------------------


class _CollectingIPC:
    """IPCOutbound fake: routes through T4's serialize_measurement_row so
    AC#5 (row shape matches T4 validator) is exercised end-to-end.

    Stores every (dim_id, serialized_row) tuple, plus a final END_OF_SCAN
    marker, so assertions can read back what crossed the IPC boundary.
    """

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []
        self.end_of_scan_sent: bool = False

    def send_row(self, dim_id: str, row: dict[str, Any]) -> None:
        payload = serialize_measurement_row(dim_id, row)
        self.rows.append((dim_id, payload))

    def send_end_of_scan(self) -> None:
        self.end_of_scan_sent = True


# ---------------------------------------------------------------------------
# Corpus fixtures.
# ---------------------------------------------------------------------------


def _build_corpus(path: Path) -> None:
    """Tiny synthetic corpus tarball: deterministic content for hashing."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data in (
            ("manifest.json", b'{"corpus_version":"0.1.0"}\n'),
            ("probes/p1.yaml", b"id: P1.1\nprompt: hello\n"),
            ("probes/p2.yaml", b"id: P2.1\nprompt: hi\n"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.fixture
def corpus_tarball(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.tar.gz"
    _build_corpus(path)
    return path


@pytest.fixture
def corpus_hash(corpus_tarball: Path) -> str:
    return compute_corpus_snapshot_hash(corpus_tarball)


# ---------------------------------------------------------------------------
# Vault fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def loaded_vault():
    vault = CredentialVault()
    vault.register(CRED_REF, SECRET_LITERAL)
    yield vault
    vault.forget_all()


def _scan_request(corpus_hash: str, **overrides: Any) -> ScanRequest:
    base = dict(
        scan_id="scan-test-1",
        tenant_id="tenant-001",
        agent_endpoint="https://agent.example.com/api",
        credential_ref=CRED_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        seed=42,
        requested_pillars=("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"),
        scan_mode="live_probe",
    )
    base.update(overrides)
    return ScanRequest(**base)


def _two_dim_probe_run_fn(runner):
    """Reference probe_run_fn: yields two HOSTED_RUNNER-shaped rows.

    The orchestrator stamps ``runner=HOSTED_RUNNER`` + ``eval_run_id``
    itself, so this generator yields only the dim_id + raw + evidence
    pair. ``runner.complete`` is invoked once per dim to prove the runner
    is driven (the result string is discarded — scoring is the Scorer's
    job, not T2's).
    """
    for dim_id, prompt in (("P1.1", "hello"), ("P2.1", "hi")):
        _ = runner.complete(prompt)
        yield dim_id, {
            "raw": 3.5 if dim_id == "P1.1" else 2.0,
            "evidence_uri": f"https://evidence.local/{dim_id}",
            "evidence_sha256": "sha256:" + "a" * 64,
        }


# ---------------------------------------------------------------------------
# AC#1 — public surface returns ScanResult.
# ---------------------------------------------------------------------------


def test_run_scan_returns_scan_result(loaded_vault, corpus_tarball, corpus_hash):
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: f"echo:{p}")

    result = run_scan(
        _scan_request(corpus_hash),
        loaded_vault,
        ipc,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_two_dim_probe_run_fn,
        runner=runner,
    )

    assert isinstance(result, ScanResult)
    assert result.scan_id == "scan-test-1"
    assert result.status == "complete"
    assert result.scanner_version == "0.3.0"
    assert result.corpus_version == "0.1.0"
    # K-replicate default (T5 / BEAAA-544): 5 passes × 2 dims = 10 rows.
    assert result.rows_emitted == 10
    assert ipc.end_of_scan_sent is True
    assert len(ipc.rows) == 10


# ---------------------------------------------------------------------------
# AC#2 — corpus_snapshot_hash mismatch raises CorpusSnapshotMismatchError.
# ---------------------------------------------------------------------------


def test_corpus_snapshot_mismatch_raises_typed_error(
    loaded_vault, corpus_tarball, corpus_hash,
):
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "")
    bogus_hash = "sha256:" + "f" * 64

    with pytest.raises(CorpusSnapshotMismatchError) as exc_info:
        run_scan(
            _scan_request(corpus_hash, corpus_snapshot_hash=bogus_hash),
            loaded_vault,
            ipc,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=_two_dim_probe_run_fn,
            runner=runner,
        )

    msg = str(exc_info.value)
    assert "corpus_snapshot_mismatch" in msg
    assert bogus_hash in msg
    assert corpus_hash in msg
    assert ipc.rows == [], "no rows should reach IPC on mismatch"
    assert ipc.end_of_scan_sent is False


def test_corpus_snapshot_mismatch_still_forgets_credential(
    loaded_vault, corpus_tarball, corpus_hash,
):
    """AC#3 — vault.forget runs even when control never reaches resolve."""
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "")
    bogus_hash = "sha256:" + "f" * 64

    with pytest.raises(CorpusSnapshotMismatchError):
        run_scan(
            _scan_request(corpus_hash, corpus_snapshot_hash=bogus_hash),
            loaded_vault,
            ipc,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=_two_dim_probe_run_fn,
            runner=runner,
        )

    assert loaded_vault.resolve(CRED_REF) is None, (
        "credential must be forgotten on every exit path, including the "
        "corpus-mismatch branch (AC#3)"
    )


# ---------------------------------------------------------------------------
# AC#3 — vault lifecycle: forget on success and on exception.
# ---------------------------------------------------------------------------


def test_vault_forget_runs_on_success(loaded_vault, corpus_tarball, corpus_hash):
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    run_scan(
        _scan_request(corpus_hash),
        loaded_vault,
        ipc,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_two_dim_probe_run_fn,
        runner=runner,
    )

    assert loaded_vault.resolve(CRED_REF) is None


def test_vault_forget_runs_on_mid_scan_exception(
    loaded_vault, corpus_tarball, corpus_hash,
):
    """A forced exception mid-scan still results in vault.forget firing."""
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    class _Boom(RuntimeError):
        pass

    def exploding_probe_fn(runner):
        # First row succeeds.
        yield "P1.1", {
            "raw": 1.0,
            "evidence_uri": "https://evidence.local/P1.1",
            "evidence_sha256": "sha256:" + "a" * 64,
        }
        raise _Boom("forced mid-scan failure")

    with pytest.raises(_Boom):
        run_scan(
            _scan_request(corpus_hash),
            loaded_vault,
            ipc,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=exploding_probe_fn,
            runner=runner,
        )

    assert loaded_vault.resolve(CRED_REF) is None, (
        "credential must be forgotten even when probe execution raises (AC#3)"
    )


def test_credential_resolve_miss_raises_typed_error(corpus_tarball, corpus_hash):
    """An empty vault → CredentialResolveError, not a silent runner call."""
    empty_vault = CredentialVault()
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    try:
        with pytest.raises(CredentialResolveError):
            run_scan(
                _scan_request(corpus_hash),
                empty_vault,
                ipc,
                corpus_tarball_path=corpus_tarball,
                probe_run_fn=_two_dim_probe_run_fn,
                runner=runner,
            )
        assert ipc.rows == []
    finally:
        empty_vault.forget_all()


# ---------------------------------------------------------------------------
# AC#4 — HOSTED_RUNNER provenance from intake_guard constant.
# ---------------------------------------------------------------------------


def test_every_emitted_row_carries_hosted_runner_provenance(
    loaded_vault, corpus_tarball, corpus_hash,
):
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    run_scan(
        _scan_request(corpus_hash),
        loaded_vault,
        ipc,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_two_dim_probe_run_fn,
        runner=runner,
    )

    assert HOSTED_RUNNER == "beaai-hosted-eval"  # canon (Δ1 backstop)
    for dim_id, row in ipc.rows:
        assert row["runner"] == HOSTED_RUNNER, (
            f"{dim_id}: provenance must be HOSTED_RUNNER, got {row['runner']!r}"
        )


def test_orchestrator_overrides_misstamped_runner_field(
    loaded_vault, corpus_tarball, corpus_hash,
):
    """A misbehaving probe_run_fn that stamps the wrong runner is corrected.

    Defense-in-depth: even if a downstream wrapper sets ``runner`` to
    something else (e.g. its own benchmark id), the orchestrator
    overwrites it with HOSTED_RUNNER before the row crosses the IPC
    boundary.
    """
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    def misbehaving(_runner):
        yield "P1.1", {
            "raw": 1.0,
            "runner": "some-other-runner",  # would have been blocked by the Scorer
            "evidence_uri": "https://evidence.local/P1.1",
            "evidence_sha256": "sha256:" + "a" * 64,
        }

    run_scan(
        _scan_request(corpus_hash),
        loaded_vault,
        ipc,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=misbehaving,
        runner=runner,
    )

    assert ipc.rows[0][1]["runner"] == HOSTED_RUNNER


# ---------------------------------------------------------------------------
# AC#5 — IPC row shape matches T4 validator.
# ---------------------------------------------------------------------------


def test_emitted_rows_pass_t4_inbound_validator(
    loaded_vault, corpus_tarball, corpus_hash,
):
    """Every row emitted by the orchestrator must round-trip through T4's
    ``validate_inbound`` — the same validator the Scorer process runs."""
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    run_scan(
        _scan_request(corpus_hash),
        loaded_vault,
        ipc,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_two_dim_probe_run_fn,
        runner=runner,
    )

    for dim_id, row in ipc.rows:
        validated_dim_id, entry = validate_inbound(row)
        assert validated_dim_id == dim_id
        assert entry["runner"] == HOSTED_RUNNER
        assert entry["eval_run_id"]


def test_eval_run_id_is_shared_across_rows_in_one_scan(
    loaded_vault, corpus_tarball, corpus_hash,
):
    """All rows from a single ``run_scan`` call share one eval_run_id —
    the forensic bundle (T10) groups them on retrieval by this id."""
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    run_scan(
        _scan_request(corpus_hash),
        loaded_vault,
        ipc,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_two_dim_probe_run_fn,
        runner=runner,
    )

    eval_run_ids = {row["eval_run_id"] for _dim, row in ipc.rows}
    assert len(eval_run_ids) == 1


def test_serializer_rejects_credential_shaped_field_inside_partial_row(
    loaded_vault, corpus_tarball, corpus_hash,
):
    """If a probe_run_fn ever tried to smuggle the credential through the
    partial_row, T4's serializer (called inside ipc.send_row) rejects it
    BEFORE the row reaches the queue. Hard Invariant #4 backstop.
    """
    ipc = _CollectingIPC()
    runner = MockRunner("test", lambda p: "x")

    def smuggling(_runner):
        yield "P1.1", {
            "raw": 1.0,
            "secret": SECRET_LITERAL,  # not in allow-list
            "evidence_uri": "https://evidence.local/P1.1",
            "evidence_sha256": "sha256:" + "a" * 64,
        }

    with pytest.raises(IPCSchemaError):
        run_scan(
            _scan_request(corpus_hash),
            loaded_vault,
            ipc,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=smuggling,
            runner=runner,
        )

    # AC#3 — even on serializer-raised exception, vault.forget runs.
    assert loaded_vault.resolve(CRED_REF) is None


# ---------------------------------------------------------------------------
# Runner construction — adapter-agnostic by design.
# ---------------------------------------------------------------------------


def test_missing_runner_and_factory_raises_orchestrator_error(
    loaded_vault, corpus_tarball, corpus_hash,
):
    ipc = _CollectingIPC()

    with pytest.raises(OrchestratorError):
        run_scan(
            _scan_request(corpus_hash),
            loaded_vault,
            ipc,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=_two_dim_probe_run_fn,
            # neither runner nor runner_factory supplied
        )

    # AC#3 — vault.forget still runs on the OrchestratorError path.
    assert loaded_vault.resolve(CRED_REF) is None


def test_runner_factory_receives_endpoint_and_secret(
    loaded_vault, corpus_tarball, corpus_hash,
):
    """Production wiring path: factory is invoked with the endpoint URL +
    the resolved secret. The secret is the in-memory string from the
    vault; nothing else has access to it."""
    captured: dict[str, Any] = {}
    ipc = _CollectingIPC()

    def factory(endpoint: str, secret: str) -> MockRunner:
        captured["endpoint"] = endpoint
        captured["secret"] = secret
        return MockRunner("factory", lambda p: "x")

    run_scan(
        _scan_request(corpus_hash),
        loaded_vault,
        ipc,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_two_dim_probe_run_fn,
        runner_factory=factory,
    )

    assert captured["endpoint"] == "https://agent.example.com/api"
    assert captured["secret"] == SECRET_LITERAL


# ---------------------------------------------------------------------------
# Provenance: HOSTED_RUNNER constant is imported, not literal-stringed.
# ---------------------------------------------------------------------------


def test_hosted_runner_constant_is_imported_from_intake_guard():
    """AC#4 — the orchestrator's HOSTED_RUNNER value IS api.intake_guard.HOSTED_RUNNER
    (same object), not a coincidental literal duplicate."""
    from scanners import live_probe_orchestrator
    from api import intake_guard

    assert live_probe_orchestrator.HOSTED_RUNNER is intake_guard.HOSTED_RUNNER
