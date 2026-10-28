"""BEAAA-43933 — judged Starter cases that could not run make the scan incomplete.

Live scan 3bf9027c recorded 33 finished + 67 transport-failed cases yet
returned ``status=complete`` / ``incomplete=false``. A ``could_not_run`` case
is processed (it still counts toward ``rows_emitted`` /
``completed_test_count``) but carries no usable evidence, so the result must
say incomplete and the existing ``partial_completion`` /
``confidence_below_adjudication_threshold`` flags must follow. An evaluated
adverse verdict (``needs_attention``) is evidence, not an unavailable case.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

from api.accepted_scan_store import AcceptedScanStore
from api.scan_handlers import ScanRuntime
from api.scan_response_envelopes import customer_payload
from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners import live_probe_orchestrator as orch
from scanners.live_probe_orchestrator import ScanRequest, run_scan
from scanners.live_probe_workers import serialize_measurement_row
from scanners.snapshot_hash import compute_corpus_snapshot_hash
from scanners.starter_judge import COULD_NOT_RUN, starter_score

CRED_REF = "cred-beaaa43933"
PACK_ID = "starter-100@5"


class _IPC:
    def send_row(self, dim_id, row) -> None:
        serialize_measurement_row(dim_id, row)

    def send_end_of_scan(self) -> None:
        pass


class _StarterRunner(MockRunner):
    def __init__(self) -> None:
        super().__init__("test", lambda prompt: "ok")
        self.rows: list[dict[str, str]] = []

    def record_starter_verdict(self, *, test_id, family, verdict, reason=""):
        self.rows.append(
            {"pack_id": PACK_ID, "test_id": test_id, "family": family,
             "verdict": verdict, "reason": reason}
        )

    def starter_result_rows(self):
        return tuple(dict(row) for row in self.rows)


def _corpus(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "corpus.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = b'{"corpus_version":"0.1.0"}\n'
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path, compute_corpus_snapshot_hash(path)


def _request(corpus_hash: str, scan_id: str = "scan-beaaa43933") -> ScanRequest:
    return ScanRequest(
        scan_id=scan_id,
        tenant_id="tenant-001",
        agent_endpoint="https://agent.example.com/api",
        credential_ref=CRED_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.1.0",
        corpus_version="0.1.0",
        seed=42,
        scan_mode="live_probe",
        pack_id=PACK_ID,
        include_result_rows=True,
    )


def _run(tmp_path: Path, verdicts: list[str], scan_id: str = "scan-beaaa43933"):
    corpus, corpus_hash = _corpus(tmp_path)

    def probe_run_fn(runner):
        for index, verdict in enumerate(verdicts):
            runner.record_starter_verdict(
                test_id=f"T{index:03d}", family="in_domain_normal",
                verdict=verdict, reason="judge reason" if verdict != COULD_NOT_RUN
                else "customer endpoint transport error",
            )
            yield "P1.1", {
                "raw": 3.0 if verdict == "passed" else 0.0,
                "evidence_uri": f"https://evidence.local/T{index:03d}",
                "evidence_sha256": "sha256:" + "a" * 64,
            }

    vault = CredentialVault()
    vault.register(CRED_REF, "sk-test-not-a-real-secret")
    request = _request(corpus_hash, scan_id)
    result = run_scan(
        request, vault, _IPC(),
        corpus_tarball_path=corpus,
        probe_run_fn=probe_run_fn,
        runner=_StarterRunner(),
    )
    return request, result


def test_reason_literal_mirrors_judge_verdict():
    assert orch.STARTER_COULD_NOT_RUN == COULD_NOT_RUN


def test_mixed_usable_and_transport_failed_cases_are_incomplete(tmp_path):
    verdicts = ["passed"] * 33 + [COULD_NOT_RUN] * 67
    _, result = _run(tmp_path, verdicts)

    assert result.status == "incomplete"
    assert result.incomplete is True
    assert result.incomplete_reason == orch.STARTER_CASES_UNAVAILABLE_REASON
    # Processed-case bookkeeping is unchanged: every case was attempted.
    assert result.rows_emitted == 100
    assert len(result.starter_result_rows) == 100
    payload = customer_payload(result)
    assert payload["partial_completion"] is True
    assert payload["confidence_below_adjudication_threshold"] is True
    assert payload["incomplete_reason"] == "starter_cases_unavailable"
    # Scoring is untouched: could_not_run stays excluded from the score.
    score = payload["starter_score"]
    assert score == starter_score(payload["result_rows"])
    assert score["ran"] == 33 and score["could_not_run"] == 67


def test_all_usable_cases_including_adverse_verdicts_stay_complete(tmp_path):
    verdicts = ["passed"] * 60 + ["needs_attention"] * 38 + ["not_applicable"] * 2
    _, result = _run(tmp_path, verdicts)

    assert result.status == "complete"
    assert result.incomplete is False
    assert result.incomplete_reason is None
    payload = customer_payload(result)
    assert payload["partial_completion"] is False
    assert payload["confidence_below_adjudication_threshold"] is False


def test_all_unavailable_cases_are_incomplete(tmp_path):
    _, result = _run(tmp_path, [COULD_NOT_RUN] * 100)

    assert result.status == "incomplete"
    assert result.incomplete is True
    assert result.incomplete_reason == "starter_cases_unavailable"
    assert result.rows_emitted == 100
    assert customer_payload(result)["partial_completion"] is True


def test_incomplete_flag_survives_persistence_reload_and_callback(tmp_path):
    verdicts = ["passed"] * 33 + [COULD_NOT_RUN] * 67
    request, result = _run(tmp_path, verdicts, scan_id="scan-beaaa43933-persist")
    db = tmp_path / "accepted.sqlite3"
    store = AcceptedScanStore(db)
    outbox = MagicMock()
    runtime = ScanRuntime(
        run_scan_fn=lambda *_: result,
        accepted_scan_store=store,
        webhook_outbox=outbox,
    )
    webhook_cfg = {"url": "https://intake.example/callback", "secret_ref": "wh"}
    store.accept(request, agent_id=None, invocation_context=None, webhook_cfg=webhook_cfg)
    runtime._webhook_cfgs[request.scan_id] = webhook_cfg

    runtime._run_accepted_scan_background(
        scan_request=request, secret="secret", agent_id=None
    )

    reloaded = AcceptedScanStore(db).get(request.scan_id)
    assert reloaded["state"] == "terminal"
    persisted = reloaded["result"]
    assert persisted.status == "incomplete"
    assert persisted.incomplete is True
    assert persisted.incomplete_reason == "starter_cases_unavailable"
    assert persisted.rows_emitted == 100
    assert len(persisted.starter_result_rows) == 100
    # Processed count stays the processed count (not redefined as usable).
    assert reloaded["completed_test_count"] == 100
    reloaded_payload = customer_payload(persisted)
    assert reloaded_payload["partial_completion"] is True
    assert reloaded_payload["confidence_below_adjudication_threshold"] is True
    assert reloaded_payload["starter_score"] == customer_payload(result)["starter_score"]

    outbox.enqueue.assert_called_once()
    callback = outbox.enqueue.call_args.kwargs["payload"]
    assert callback["partial_completion"] is True
    assert callback["confidence_below_adjudication_threshold"] is True
