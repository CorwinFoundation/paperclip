"""BEAAA-11372 human-lane determination review queue.

Graded autonomy: v1 routes covered-peril candidates (and boundary
insufficient-evidence referrals) to a human reviewer before any coverage ruling.
The queue holds lightweight entries that REFERENCE the determination record by
``determination_id``; the determination record in the audit spine remains the
book of record. A reviewer's ruling is captured as a resolution appended to the
audit spine, never as an in-place edit of the determination record.

The queue is intentionally storage-agnostic: production may back it with existing
Paperclip tasking/queue primitives (one review task per queued entry). The
``InMemoryReviewQueue`` below is the reference implementation used in tests and
dry runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from determination.records import DeterminationRecord

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_RESOLVED = "resolved"


@dataclass(frozen=True)
class ReviewQueueEntry:
    determination_id: str
    peril: str | None
    final_decision: str
    review_owner: str
    evidence_batch_id: str
    tenant_id_hash: str
    status: str = STATUS_PENDING
    reviewer: str | None = None
    resolution: dict[str, Any] | None = None

    def with_status(
        self,
        status: str,
        *,
        reviewer: str | None = None,
        resolution: dict[str, Any] | None = None,
    ) -> "ReviewQueueEntry":
        return ReviewQueueEntry(
            determination_id=self.determination_id,
            peril=self.peril,
            final_decision=self.final_decision,
            review_owner=self.review_owner,
            evidence_batch_id=self.evidence_batch_id,
            tenant_id_hash=self.tenant_id_hash,
            status=status,
            reviewer=reviewer if reviewer is not None else self.reviewer,
            resolution=resolution if resolution is not None else self.resolution,
        )


def entry_for(record: DeterminationRecord) -> ReviewQueueEntry:
    ref = record.evidence_packet_ref
    return ReviewQueueEntry(
        determination_id=record.determination_id,
        peril=record.peril,
        final_decision=record.final_decision,
        review_owner=record.review_owner,
        evidence_batch_id=str(ref.get("evidence_batch_id")),
        tenant_id_hash=str(ref.get("tenant_id_hash")),
    )


class HumanReviewQueue(Protocol):
    def enqueue(self, record: DeterminationRecord) -> ReviewQueueEntry | None: ...
    def pending(self) -> tuple[ReviewQueueEntry, ...]: ...


class InMemoryReviewQueue:
    """Reference human-lane review queue keyed by determination_id."""

    def __init__(self) -> None:
        self._entries: dict[str, ReviewQueueEntry] = {}

    def enqueue(self, record: DeterminationRecord) -> ReviewQueueEntry | None:
        # Only records that need human review are queued; no-finding records are
        # book-of-record only and are not routed to a reviewer.
        if not record.review_required:
            return None
        entry = entry_for(record)
        # Idempotent on determination_id: re-enqueueing the same determination
        # does not create a duplicate review task.
        self._entries.setdefault(entry.determination_id, entry)
        return self._entries[entry.determination_id]

    def enqueue_all(
        self, records: Iterable[DeterminationRecord]
    ) -> list[ReviewQueueEntry]:
        return [e for r in records if (e := self.enqueue(r)) is not None]

    def claim(self, determination_id: str, reviewer: str) -> ReviewQueueEntry:
        entry = self._require(determination_id)
        updated = entry.with_status(STATUS_CLAIMED, reviewer=reviewer)
        self._entries[determination_id] = updated
        return updated

    def resolve(
        self, determination_id: str, *, reviewer: str, ruling: dict[str, Any]
    ) -> ReviewQueueEntry:
        entry = self._require(determination_id)
        updated = entry.with_status(
            STATUS_RESOLVED, reviewer=reviewer, resolution=dict(ruling)
        )
        self._entries[determination_id] = updated
        return updated

    def pending(self) -> tuple[ReviewQueueEntry, ...]:
        return tuple(
            e for e in self._entries.values() if e.status != STATUS_RESOLVED
        )

    def all_entries(self) -> tuple[ReviewQueueEntry, ...]:
        return tuple(self._entries.values())

    def _require(self, determination_id: str) -> ReviewQueueEntry:
        if determination_id not in self._entries:
            raise KeyError(f"no review entry for determination {determination_id}")
        return self._entries[determination_id]


__all__ = [
    "HumanReviewQueue",
    "InMemoryReviewQueue",
    "ReviewQueueEntry",
    "STATUS_CLAIMED",
    "STATUS_PENDING",
    "STATUS_RESOLVED",
    "entry_for",
]
