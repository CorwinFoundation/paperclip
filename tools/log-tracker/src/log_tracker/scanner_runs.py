"""Scanner-run write hook for Log Tracker v0.

BEAAA-6159 child C. Rule-execution: this module consumes the finalized
BackBond Scanner output envelope and writes an additive tracker record.
It does not import scoring, rubric, pricing, or band logic.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


ArtifactUriResolver = Callable[[str], bool]


class ArtifactUriUnreachable(RuntimeError):
    """Raised when a non-N/A evidence ref has an unreachable artifact_uri."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scanner_runs (
    scan_id              TEXT PRIMARY KEY,
    agent_id             TEXT NOT NULL,
    run_timestamp        TEXT NOT NULL,
    scanner_version      TEXT NOT NULL,
    corpus_version       TEXT,
    redaction_class      TEXT NOT NULL,
    tracker_envelope     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_refs (
    scan_id              TEXT NOT NULL,
    ordinal              INTEGER NOT NULL,
    rubric_point_id      TEXT NOT NULL,
    test_id              TEXT,
    artifact_uri         TEXT,
    verdict              TEXT,
    weight               REAL,
    ref_json             TEXT NOT NULL,
    PRIMARY KEY (scan_id, ordinal),
    FOREIGN KEY (scan_id) REFERENCES scanner_runs(scan_id) ON DELETE CASCADE
);
"""


def build_scanner_run_envelope(
    *,
    agent_id: str,
    scan_id: str,
    run_timestamp: str,
    score_json: Mapping[str, Any],
    redaction_class: str = "hash",
) -> dict[str, Any]:
    """Return the tracker envelope for one completed Scanner run.

    The embedded ``score_json`` fields are copied from the already-finalized
    contract envelope. No score, band, confidence, or subscore is recomputed.
    """
    evidence_refs = copy.deepcopy(score_json.get("evidence_refs", []))
    if not isinstance(evidence_refs, list):
        raise ValueError("score_json.evidence_refs must be a list")
    return {
        "event_kind": "scanner_run.completed",
        "contract_ref": "are-scanner-api-v1@v1.0",
        "agent_id": agent_id,
        "scan_id": scan_id,
        "run_timestamp": run_timestamp,
        "scanner_version": score_json.get("scanner_version"),
        "corpus_version": score_json.get("corpus_version"),
        "probe_set_version": (
            score_json.get("probe_set_version")
            or score_json.get("probe-set-version")
            or score_json.get("rubric_version")
            or "n/a"
        ),
        "rubric_point_id": _rubric_point_range(evidence_refs),
        "redaction_class": redaction_class,
        "are_total": score_json.get("are_total"),
        "subscores": copy.deepcopy(score_json.get("subscores")),
        "band": score_json.get("band"),
        "confidence": score_json.get("confidence"),
        "evidence_refs": evidence_refs,
    }


class LogTracker:
    """Sqlite-backed Log Tracker v0 writer for scanner-run events."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        artifact_uri_resolver: ArtifactUriResolver | None = None,
    ) -> None:
        self._conn = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._artifact_uri_resolver = artifact_uri_resolver or _default_uri_resolver

    def emit_scanner_run_completed(
        self,
        *,
        agent_id: str,
        scan_id: str,
        run_timestamp: str,
        score_json: Mapping[str, Any],
        redaction_class: str = "hash",
    ) -> dict[str, Any]:
        """Commit ``scanner_runs`` + all ``evidence_refs`` atomically.

        If any non-N/A evidence ref fails the artifact URI resolver, the
        transaction rolls back and no partial scanner-run row remains.
        """
        envelope = build_scanner_run_envelope(
            agent_id=agent_id,
            scan_id=scan_id,
            run_timestamp=run_timestamp,
            score_json=score_json,
            redaction_class=redaction_class,
        )
        evidence_refs = envelope["evidence_refs"]

        with _transaction(self._conn):
            self._conn.execute(
                "INSERT INTO scanner_runs "
                "(scan_id, agent_id, run_timestamp, scanner_version, "
                "corpus_version, redaction_class, tracker_envelope) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    agent_id,
                    run_timestamp,
                    envelope["scanner_version"],
                    envelope["corpus_version"],
                    redaction_class,
                    json.dumps(envelope, sort_keys=True),
                ),
            )
            for ordinal, ref in enumerate(evidence_refs):
                if not isinstance(ref, Mapping):
                    raise ValueError("evidence_refs entries must be objects")
                artifact_uri = ref.get("artifact_uri")
                if _must_resolve(ref) and (
                    not isinstance(artifact_uri, str)
                    or not self._artifact_uri_resolver(artifact_uri)
                ):
                    raise ArtifactUriUnreachable(
                        f"unreachable artifact_uri for scan_id={scan_id!r}: "
                        f"{artifact_uri!r}"
                    )
                self._conn.execute(
                    "INSERT INTO evidence_refs "
                    "(scan_id, ordinal, rubric_point_id, test_id, artifact_uri, "
                    "verdict, weight, ref_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scan_id,
                        ordinal,
                        str(ref.get("rubric_point_id", "")),
                        _optional_str(ref.get("test_id")),
                        _optional_str(artifact_uri),
                        _optional_str(ref.get("verdict")),
                        _optional_float(ref.get("weight")),
                        json.dumps(ref, sort_keys=True),
                    ),
                )
        return envelope

    def get_scanner_run(self, scan_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM scanner_runs WHERE scan_id = ?", (scan_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        out = {k: row[k] for k in row.keys()}
        out["tracker_envelope"] = json.loads(out["tracker_envelope"])
        return out

    def list_evidence_refs(self, scan_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM evidence_refs WHERE scan_id = ? ORDER BY ordinal ASC",
            (scan_id,),
        )
        rows = []
        for row in cur.fetchall():
            out = {k: row[k] for k in row.keys()}
            out["ref_json"] = json.loads(out["ref_json"])
            rows.append(out)
        return rows

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LogTracker":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _must_resolve(ref: Mapping[str, Any]) -> bool:
    return ref.get("verdict") != "n/a"


def _default_uri_resolver(uri: str) -> bool:
    return bool(uri.strip())


def _rubric_point_range(evidence_refs: list[Any]) -> str:
    ids = sorted(
        {
            str(ref.get("rubric_point_id"))
            for ref in evidence_refs
            if isinstance(ref, Mapping) and ref.get("rubric_point_id")
        }
    )
    return ",".join(ids) if ids else "n/a"


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


__all__ = [
    "ArtifactUriResolver",
    "ArtifactUriUnreachable",
    "LogTracker",
    "build_scanner_run_envelope",
]
