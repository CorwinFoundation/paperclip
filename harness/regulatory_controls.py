"""Deterministic privacy and jurisdiction controls for scanner probes.

The scanner uses this small in-memory target to exercise regulatory behavior
without sending personal data to an external service.  It deliberately models
the retention boundary separately from the primary record store so deletion
tests can detect partial propagation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class BlockedJurisdictionError(PermissionError):
    """Raised before processing a request from a blocked jurisdiction."""


@dataclass(frozen=True)
class SubjectRecord:
    subject_id: str
    category: str
    value: Any


class RegulatoryProbeTarget:
    """Stateful target used by scanner coverage for data-subject controls."""

    def __init__(
        self,
        records: Iterable[SubjectRecord] = (),
        retained_artifacts: Iterable[SubjectRecord] = (),
        blocked_jurisdictions: Iterable[str] = (),
    ) -> None:
        self._records = list(records)
        self._retained_artifacts = list(retained_artifacts)
        self._blocked_jurisdictions = {
            jurisdiction.strip().upper() for jurisdiction in blocked_jurisdictions
        }

    def export_subject(self, subject_id: str) -> Mapping[str, list[dict[str, Any]]]:
        """Return every record for one subject, grouped by storage boundary."""

        def serialize(rows: Iterable[SubjectRecord]) -> list[dict[str, Any]]:
            return [
                {"category": row.category, "value": row.value}
                for row in rows
                if row.subject_id == subject_id
            ]

        return {
            "primary_records": serialize(self._records),
            "retained_artifacts": serialize(self._retained_artifacts),
        }

    def delete_subject(self, subject_id: str) -> int:
        """Delete subject data from primary and retained-artifact stores."""

        before = len(self._records) + len(self._retained_artifacts)
        self._records = [row for row in self._records if row.subject_id != subject_id]
        self._retained_artifacts = [
            row for row in self._retained_artifacts if row.subject_id != subject_id
        ]
        return before - len(self._records) - len(self._retained_artifacts)

    def handle_request(self, jurisdiction: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Refuse blocked geographies before the payload reaches processing."""

        normalized = jurisdiction.strip().upper()
        if normalized in self._blocked_jurisdictions:
            raise BlockedJurisdictionError(
                f"requests from jurisdiction {normalized} are blocked by policy"
            )
        return {"status": "accepted", "payload": dict(payload)}
