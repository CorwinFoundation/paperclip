"""BEAAA-1271 — WS2 persistence layer tests.

Covers ADR-0009 §4 invariants:
  §4.1 write-once ``forensic_bundle_uri`` (DB constraint + API 409 surface)
  §4.2 at-write-time RFC 8785 ``canonical_sha256`` ``snapshot_hash``
  §4   append-only (no row delete on pinned rows)
  AC4  recompute-over-stored-bundle-bytes byte-for-byte across N=5 +
       permuted key order + non-significant whitespace

Stdlib-only; no scoring path touched (per AC #5 — auditor duplicate-logic
gate is satisfied because this module does not import rubric / judge
paths).
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from api.handlers import HandlerError
from api.jcs import canonicalize
from api.scan_log import (
    ForensicBundleWriteOnce,
    ScanLog,
    ScanLogAppendOnly,
    ScanRowNotFound,
    SnapshotHashWriteOnce,
    compute_snapshot_hash,
)
from api.scan_log_handlers import finalize_pins_handler, persist_scan_handler


# A representative score envelope; exercises nested objects, lists,
# integers, floats, booleans, nulls, and an ISO-8601 timestamp string.
SCORE_ENVELOPE = {
    "are_total": 73,
    "subscores": {
        "GOVERNANCE": 18,
        "ROBUSTNESS": 19,
        "BIAS_FAIRNESS": 17,
        "OPS": 19,
    },
    "band": "B",
    "confidence": 0.92,
    "evidence_refs": [
        {
            "rubric_point_id": "ROB.PI.MULTI_TURN",
            "test_id": "t-1",
            "artifact_uri": "https://e/1",
            "verdict": "pass",
            "weight": 0.5,
        },
        {
            "rubric_point_id": "GOV.AUDIT.IMMUTABLE",
            "test_id": "t-2",
            "artifact_uri": "https://e/2",
            "verdict": "partial",
            "weight": 0.25,
        },
    ],
    "uncovered_dimensions": [],
    "scanner_version": "0.3.6",
    "corpus_version": "1.2.0",
    "model_versions": {"primary": "m-1", "judges": ["j-1", "j-2"]},
    "seed": 42,
    "run_timestamp": "2026-05-30T12:00:00Z",
    "partial_completion": False,
    "confidence_below_adjudication_threshold": False,
}


def _permute_keys(value):
    """Return a deep copy of `value` with object keys in reverse order.

    Drives the AC #4 assertion that JCS canonicalization is insensitive
    to input key-iteration order — equivalent to "permute non-canonical
    key order ... recompute must still match".
    """
    if isinstance(value, dict):
        permuted = {}
        for k in reversed(list(value.keys())):
            permuted[k] = _permute_keys(value[k])
        return permuted
    if isinstance(value, list):
        return [_permute_keys(v) for v in value]
    return value


class JCSStabilityTests(unittest.TestCase):
    """RFC 8785 properties the snapshot_hash recipe relies on."""

    def test_key_order_does_not_change_hash(self):
        h1 = compute_snapshot_hash(SCORE_ENVELOPE)
        h2 = compute_snapshot_hash(_permute_keys(SCORE_ENVELOPE))
        self.assertEqual(h1, h2)

    def test_non_significant_whitespace_does_not_change_hash(self):
        # An indented / pretty-printed serialization round-trips through
        # ``json.loads`` to a dict identical (up to ordering) to the source;
        # the JCS-canonicalized form is whitespace-free by construction.
        as_json_pretty = json.dumps(SCORE_ENVELOPE, indent=4)
        as_json_compact = json.dumps(SCORE_ENVELOPE, separators=(",", ":"))
        h_source = compute_snapshot_hash(SCORE_ENVELOPE)
        h_pretty = compute_snapshot_hash(json.loads(as_json_pretty))
        h_compact = compute_snapshot_hash(json.loads(as_json_compact))
        self.assertEqual(h_source, h_pretty)
        self.assertEqual(h_source, h_compact)

    def test_n5_recompute_byte_for_byte_stable(self):
        # AC #4: "fixture envelope → compute hash → assert equality across
        # N=5 recomputes". Deterministic over a pure function.
        hashes = {compute_snapshot_hash(SCORE_ENVELOPE) for _ in range(5)}
        self.assertEqual(len(hashes), 1)

    def test_hash_is_sensitive_to_meaningful_changes(self):
        # Negative control: the hash MUST change when a load-bearing
        # field changes. If this passes when it shouldn't, the JCS
        # implementation is collapsing distinct envelopes.
        moved = dict(SCORE_ENVELOPE)
        moved["are_total"] = 74
        self.assertNotEqual(
            compute_snapshot_hash(SCORE_ENVELOPE),
            compute_snapshot_hash(moved),
        )

    def test_format_is_sha256_hex(self):
        digest = compute_snapshot_hash(SCORE_ENVELOPE)
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(len(digest), len("sha256:") + 64)
        int(digest[len("sha256:"):], 16)  # parses as hex


class ScanLogPersistenceTests(unittest.TestCase):
    """Storage-layer invariants — write-once URI, write-once hash,
    append-only rows, indexed chain reconstruction."""

    def setUp(self):
        self.log = ScanLog()

    def tearDown(self):
        self.log.close()

    def test_append_then_read_persists_hash_and_uri(self):
        h = self.log.append_scan(
            agent_id="a-1",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-1",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
            forensic_bundle_uri="https://b/1",
        )
        row = self.log.get("a-1", "2026-05-30T12:00:00Z")
        self.assertIsNotNone(row)
        self.assertEqual(row["forensic_bundle_uri"], "https://b/1")
        self.assertEqual(row["snapshot_hash"], h)

    def test_recompute_over_persisted_bundle_bytes_matches_stored_hash(self):
        # AC #4: "Recompute over persisted bundle bytes via JCS
        # reproduces the stored hash byte-for-byte."
        h = self.log.append_scan(
            agent_id="a-2",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-2",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
            forensic_bundle_uri="https://b/2",
        )
        row = self.log.get("a-2", "2026-05-30T12:00:00Z")
        recomputed = compute_snapshot_hash(row["score_json"])
        self.assertEqual(recomputed, h)

    def test_write_once_forensic_bundle_uri_via_finalize_pins(self):
        self.log.insert_pending(
            agent_id="a-3",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-3",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
        )
        self.log.finalize_pins(
            agent_id="a-3",
            run_timestamp="2026-05-30T12:00:00Z",
            forensic_bundle_uri="https://b/3",
        )
        with self.assertRaises(ForensicBundleWriteOnce):
            self.log.finalize_pins(
                agent_id="a-3",
                run_timestamp="2026-05-30T12:00:00Z",
                forensic_bundle_uri="https://b/3-second",
            )

    def test_write_once_forensic_bundle_uri_via_direct_db_update(self):
        # AC #2: "DB-level constraint enforces write-once at the DB
        # layer; second UPDATE attempt returns DB error". This bypasses
        # the Python API and hits the trigger directly.
        self.log.append_scan(
            agent_id="a-4",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-4",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
            forensic_bundle_uri="https://b/4",
        )
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.log._conn.execute(
                "UPDATE scan_log SET forensic_bundle_uri = ? "
                "WHERE agent_id = ? AND run_timestamp = ?",
                ("https://b/4-second", "a-4", "2026-05-30T12:00:00Z"),
            )
        self.assertIn("forensic_bundle_uri_write_once", str(ctx.exception))

    def test_snapshot_hash_write_once_via_direct_db_update(self):
        self.log.append_scan(
            agent_id="a-5",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-5",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
            forensic_bundle_uri="https://b/5",
        )
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.log._conn.execute(
                "UPDATE scan_log SET snapshot_hash = ? "
                "WHERE agent_id = ? AND run_timestamp = ?",
                ("sha256:" + "0" * 64, "a-5", "2026-05-30T12:00:00Z"),
            )
        self.assertIn("snapshot_hash_write_once", str(ctx.exception))

    def test_append_only_blocks_delete_on_pinned_rows(self):
        self.log.append_scan(
            agent_id="a-6",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-6",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
            forensic_bundle_uri="https://b/6",
        )
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.log._conn.execute(
                "DELETE FROM scan_log WHERE agent_id = ?", ("a-6",),
            )
        self.assertIn("scan_log_append_only", str(ctx.exception))

    def test_pending_row_delete_allowed_so_aborted_scans_can_be_swept(self):
        # Append-only applies to *pinned* rows; an insert_pending row
        # that never got pinned (a crashed scan) must remain
        # garbage-collectable.
        self.log.insert_pending(
            agent_id="a-7",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-7",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
        )
        self.log._conn.execute(
            "DELETE FROM scan_log WHERE agent_id = ?", ("a-7",),
        )
        self.assertIsNone(self.log.get("a-7", "2026-05-30T12:00:00Z"))

    def test_list_for_agent_returns_chain_in_ascending_timestamp_order(self):
        # WS3 chain reconstruction lookup relies on this ordering.
        timestamps = [
            "2026-05-30T11:00:00Z",
            "2026-05-30T14:00:00Z",
            "2026-05-30T15:00:00Z",
        ]
        for i, ts in enumerate(reversed(timestamps), start=1):
            self.log.append_scan(
                agent_id="a-chain",
                run_timestamp=ts,
                scan_id=f"s-chain-{i}",
                scanner_version="0.3.6",
                corpus_version="1.2.0",
                score_json=SCORE_ENVELOPE,
                forensic_bundle_uri=f"https://b/chain/{i}",
            )
        ordered = [r["run_timestamp"] for r in self.log.list_for_agent("a-chain")]
        self.assertEqual(ordered, timestamps)

    def test_index_on_agent_id_run_timestamp_exists(self):
        # The WS3 chain lookup depends on this index; absence is a
        # silent performance bug, so we assert it explicitly.
        cur = self.log._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("ix_scan_log_agent_ts",),
        )
        self.assertIsNotNone(cur.fetchone())

    def test_finalize_pins_on_missing_row_raises_scan_row_not_found(self):
        with self.assertRaises(ScanRowNotFound):
            self.log.finalize_pins(
                agent_id="missing",
                run_timestamp="2026-05-30T12:00:00Z",
                forensic_bundle_uri="https://b/m",
            )

    def test_durable_filesystem_path_preserves_invariants_across_reconnect(self):
        # Triggers + index must survive a process restart; the spec
        # path explicitly states DB-layer constraint, so an in-process-
        # only enforcement would be a contract bug.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "scan_log.sqlite"
            with ScanLog(db_path) as a:
                a.append_scan(
                    agent_id="a-d",
                    run_timestamp="2026-05-30T12:00:00Z",
                    scan_id="s-d",
                    scanner_version="0.3.6",
                    corpus_version="1.2.0",
                    score_json=SCORE_ENVELOPE,
                    forensic_bundle_uri="https://b/d",
                )
            with ScanLog(db_path) as b:
                with self.assertRaises(sqlite3.IntegrityError):
                    b._conn.execute(
                        "UPDATE scan_log SET forensic_bundle_uri = ? "
                        "WHERE agent_id = ?",
                        ("https://b/d-second", "a-d"),
                    )


class PersistScanHandlerTests(unittest.TestCase):
    """AC #3 — API edge surfaces the DB constraint as HTTP 409 with
    reason ``forensic_bundle_uri_write_once``."""

    def setUp(self):
        self.log = ScanLog()
        self.body = {
            "agent_id": "a-h",
            "run_timestamp": "2026-05-30T12:00:00Z",
            "scan_id": "s-h",
            "scanner_version": "0.3.6",
            "corpus_version": "1.2.0",
            "score_json": SCORE_ENVELOPE,
            "forensic_bundle_uri": "https://b/h",
        }

    def tearDown(self):
        self.log.close()

    def test_first_persist_returns_201_with_snapshot_hash(self):
        status, body = persist_scan_handler(self.body, scan_log=self.log)
        self.assertEqual(status, 201)
        self.assertEqual(body["forensic_bundle_uri"], "https://b/h")
        self.assertEqual(body["agent_id"], "a-h")
        self.assertEqual(
            body["snapshot_hash"], compute_snapshot_hash(SCORE_ENVELOPE)
        )

    def test_second_persist_returns_409_forensic_bundle_uri_write_once(self):
        persist_scan_handler(self.body, scan_log=self.log)
        clash = dict(self.body)
        clash["forensic_bundle_uri"] = "https://b/h-second"
        with self.assertRaises(HandlerError) as ctx:
            persist_scan_handler(clash, scan_log=self.log)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.message, "forensic_bundle_uri_write_once")

    def test_finalize_after_first_pin_also_returns_409(self):
        # The pending-then-finalize path is the canonical one for the
        # bundle-uploaded-later workflow; the 409 surface must be the
        # same.
        self.log.insert_pending(
            agent_id="a-f",
            run_timestamp="2026-05-30T12:00:00Z",
            scan_id="s-f",
            scanner_version="0.3.6",
            corpus_version="1.2.0",
            score_json=SCORE_ENVELOPE,
        )
        finalize_pins_handler(
            {
                "agent_id": "a-f",
                "run_timestamp": "2026-05-30T12:00:00Z",
                "forensic_bundle_uri": "https://b/f",
            },
            scan_log=self.log,
        )
        with self.assertRaises(HandlerError) as ctx:
            finalize_pins_handler(
                {
                    "agent_id": "a-f",
                    "run_timestamp": "2026-05-30T12:00:00Z",
                    "forensic_bundle_uri": "https://b/f-second",
                },
                scan_log=self.log,
            )
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.message, "forensic_bundle_uri_write_once")

    def test_missing_required_field_returns_422(self):
        incomplete = dict(self.body)
        del incomplete["forensic_bundle_uri"]
        with self.assertRaises(HandlerError) as ctx:
            persist_scan_handler(incomplete, scan_log=self.log)
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.message, "forensic_bundle_uri_required")


class NoRubricOrJudgeImportTests(unittest.TestCase):
    """AC #5 — auditor duplicate-logic gate: this WS must not import
    rubric / probe / judge / scoring paths into the storage layer."""

    def test_scan_log_module_imports_no_scoring_paths(self):
        import api.scan_log as mod

        source = Path(mod.__file__).read_text()
        forbidden = (
            "from rubric",
            "import rubric",
            "from scanners.live_probe",
            "from scanners.inspect_runner",
            "from scanners.pyrit_runner",
            "from scanners.promptfoo_runner",
        )
        for token in forbidden:
            self.assertNotIn(token, source,
                f"scan_log must not import scoring path {token!r}")

    def test_jcs_module_imports_no_scoring_paths(self):
        import api.jcs as mod

        source = Path(mod.__file__).read_text()
        forbidden = ("from rubric", "import rubric", "from scanners",
                     "import scanners")
        for token in forbidden:
            self.assertNotIn(token, source,
                f"jcs must not import scoring path {token!r}")


if __name__ == "__main__":
    unittest.main()
