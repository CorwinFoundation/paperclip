"""BEAAA-38253 — starter-100@1 sends exactly one request per canonical ID.

Production 37ce40eb sent 500 requests for one starter scan because the
orchestrator K-replicated the walker (DEFAULT_K_REPLICATES = 5). These tests
count real runner invocations through ``run_scan`` + the customer walker,
not roster length.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.live_probe_orchestrator import (
    DEFAULT_K_REPLICATES,
    ScanRequest,
    run_scan,
)
from scanners.live_probe_workers import serialize_measurement_row
from scanners.snapshot_hash import compute_corpus_snapshot_hash
from scanners.starter_pack import STARTER_PACK_ID, STARTER_PACK_ROSTER

CRED_REF = "cred-starter-dispatch"


class _IPC:
    def __init__(self) -> None:
        self.rows = []

    def send_row(self, dim_id, row) -> None:
        self.rows.append((dim_id, serialize_measurement_row(dim_id, row)))

    def send_end_of_scan(self) -> None:
        pass


def _corpus(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "corpus.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = b'{"corpus_version":"0.1.0"}\n'
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path, compute_corpus_snapshot_hash(path)


def _run(tmp_path: Path, **overrides):
    corpus, corpus_hash = _corpus(tmp_path)
    calls: list[str] = []

    def reply(prompt: str) -> str:
        calls.append(prompt)
        return "I can't help with that."

    request = dict(
        scan_id="scan-starter-dispatch",
        tenant_id="tenant-001",
        agent_endpoint="https://agent.example.com/api",
        credential_ref=CRED_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        seed=42,
        scan_mode="live_probe",
    )
    request.update(overrides)
    vault = CredentialVault()
    vault.register(CRED_REF, "sk-test-not-a-real-secret")
    result = run_scan(
        ScanRequest(**request),
        vault,
        _IPC(),
        corpus_tarball_path=corpus,
        probe_run_fn=customer_agent_probe_run_fn,
        runner=MockRunner("test", reply),
    )
    return result, calls


def test_starter_pack_sends_exactly_one_request_per_canonical_id(tmp_path):
    result, calls = _run(tmp_path, pack_id=STARTER_PACK_ID, scan_tier="smoke")

    assert len(STARTER_PACK_ROSTER) == 100
    assert len(calls) == 100
    sent_ids = [prompt.split()[2] for prompt in calls]
    assert sent_ids == [row.case_id for row in STARTER_PACK_ROSTER]
    assert len(set(sent_ids)) == 100
    assert result.rows_emitted == 100


def test_non_pack_scan_keeps_k_replicates(tmp_path):
    def two_dim(runner):
        for dim_id in ("P1.1", "P2.1"):
            runner.complete(dim_id)
            yield dim_id, {
                "raw": 3.0,
                "evidence_uri": f"https://evidence.local/{dim_id}",
                "evidence_sha256": "sha256:" + "a" * 64,
            }

    corpus, corpus_hash = _corpus(tmp_path)
    calls: list[str] = []
    vault = CredentialVault()
    vault.register(CRED_REF, "sk-test-not-a-real-secret")
    result = run_scan(
        ScanRequest(
            scan_id="scan-legacy",
            tenant_id="tenant-001",
            agent_endpoint="https://agent.example.com/api",
            credential_ref=CRED_REF,
            corpus_snapshot_hash=corpus_hash,
            scanner_version="0.3.0",
            corpus_version="0.1.0",
            seed=42,
            scan_mode="live_probe",
        ),
        vault,
        _IPC(),
        corpus_tarball_path=corpus,
        probe_run_fn=two_dim,
        runner=MockRunner("test", lambda p: calls.append(p) or "ok"),
    )

    assert len(calls) == 2 * DEFAULT_K_REPLICATES
    assert result.rows_emitted == 2 * DEFAULT_K_REPLICATES
