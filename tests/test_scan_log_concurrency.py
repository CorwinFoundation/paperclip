"""BEAAA-14428 — ScanLog cross-thread write serialization (F7 reliability).

Root cause: ``ScanLog`` holds ONE ``sqlite3.Connection`` for the process
lifetime (``check_same_thread=False``) and ``api/app.py`` shares a single
``ScanRuntime`` (hence one ``ScanLog``) on ``app.state``. FastAPI dispatches
sync ``/scan`` handlers on a threadpool, so concurrent scans call
``append_scan`` on the **same connection from different threads**. The
``BEGIN IMMEDIATE`` in ``_transaction`` serializes writers across *separate*
connections (a file lock); it does NOT serialize two threads sharing ONE
connection object — SQLite raises
``OperationalError: cannot start a transaction within a transaction`` and the
ADR-0009 §4 durable evidence row silently fails to write.

This test reproduces that failure against canon and pins the fix (an
app-level ``threading.RLock`` serializing all connection access). It is
stdlib-only and touches no scoring / rubric / judge path — Δ=0 on goldens
by construction.
"""
from __future__ import annotations

import threading
import unittest

from api.scan_log import ScanLog


def _envelope(i: int) -> dict:
    """A minimal-but-representative score envelope, distinct per thread."""
    return {
        "are_total": 70 + (i % 10),
        "subscores": {"GOVERNANCE": 18, "OPERATIONS": 17},
        "band": "B",
        "n": i,
    }


class ScanLogConcurrencyTest(unittest.TestCase):
    """N concurrent ``append_scan`` calls on ONE shared ScanLog must all
    persist with zero exceptions (the audit spine cannot drop rows)."""

    N_THREADS = 16

    def test_concurrent_append_scan_persists_all_rows(self) -> None:
        log = ScanLog(":memory:")
        self.addCleanup(log.close)

        # A barrier maximizes the odds every thread is inside the write
        # window simultaneously — that is when the single-connection
        # BEGIN/COMMIT interleave manifests.
        start = threading.Barrier(self.N_THREADS)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def worker(i: int) -> None:
            start.wait()
            try:
                log.append_scan(
                    agent_id=f"agent-{i}",
                    run_timestamp=f"2026-07-15T00:00:{i:02d}Z",
                    scan_id=f"scan-{i}",
                    scanner_version="0.4.1",
                    corpus_version="c1",
                    score_json=_envelope(i),
                    forensic_bundle_uri=f"file:///bundles/{i}.tar.gz",
                    probe_set_hash=f"sha256:{i:064x}",
                )
            except BaseException as exc:  # noqa: BLE001 — capture ALL failures
                with errors_lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(self.N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # (1) No thread may raise — a raised ``cannot start a transaction
        #     within a transaction`` is the exact red-line evidence-gap bug.
        self.assertEqual(
            errors,
            [],
            f"concurrent append_scan raised {len(errors)} error(s); "
            f"first: {errors[0]!r}" if errors else "unexpected",
        )

        # (2) Every row must be durably persisted.
        persisted = sum(
            len(log.list_for_agent(f"agent-{i}")) for i in range(self.N_THREADS)
        )
        self.assertEqual(
            persisted,
            self.N_THREADS,
            f"expected {self.N_THREADS} persisted rows, found {persisted}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
