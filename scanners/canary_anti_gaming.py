"""Canary probe ledger + Goodhart detector for live-probe scans.

BEAAA-11721 / ADR-0041 §3, §6. This module is deliberately sidecar-only:
it records which probe phrasings have fired for a tenant/vendor pair and
emits an Underwriting-review flag when a vendor appears to memorize known
phrasings. It does not feed scoring math.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, Sequence


CANARY_FLAG_NONE = "none"
CANARY_FLAG_GOODHART_SUSPECTED = "goodhart_suspected"
DEFAULT_GOODHART_THRESHOLD = 0.25

_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phrasing_hash(value: str) -> str:
    """Return canonical ``sha256:<lowerhex>`` phrasing hash."""
    if not isinstance(value, str) or not _HASH_RE.match(value):
        raise ValueError("phrasing hash must be sha256:<64-hex> or 64-hex")
    hex_part = value.split(":", 1)[-1].lower()
    return f"sha256:{hex_part}"


@dataclass(frozen=True)
class FiredProbeRecord:
    tenant_id: str
    vendor_id: str
    phrasing_hash: str
    scan_id: str
    fired_at: str
    rubric_point_id: str | None = None
    test_id: str | None = None


class FiredProbeLedger(Protocol):
    """Append-only fired-probe ledger boundary.

    Production storage can implement this protocol with an audit-spine table.
    The in-memory implementation below is for deterministic tests and local
    wiring. Implementations must scope reads by both tenant and vendor.
    """

    def append(self, record: FiredProbeRecord) -> None: ...

    def seen_hashes(self, *, tenant_id: str, vendor_id: str) -> frozenset[str]: ...


@dataclass
class InMemoryFiredProbeLedger:
    """Append-only tenant/vendor scoped ledger for tests and local wiring."""

    _records: list[FiredProbeRecord] = field(default_factory=list)

    def append(self, record: FiredProbeRecord) -> None:
        self._records.append(
            FiredProbeRecord(
                tenant_id=record.tenant_id,
                vendor_id=record.vendor_id,
                phrasing_hash=normalize_phrasing_hash(record.phrasing_hash),
                scan_id=record.scan_id,
                fired_at=record.fired_at,
                rubric_point_id=record.rubric_point_id,
                test_id=record.test_id,
            )
        )

    def seen_hashes(self, *, tenant_id: str, vendor_id: str) -> frozenset[str]:
        return frozenset(
            r.phrasing_hash
            for r in self._records
            if r.tenant_id == tenant_id and r.vendor_id == vendor_id
        )

    @property
    def records(self) -> tuple[FiredProbeRecord, ...]:
        return tuple(self._records)


@dataclass(frozen=True)
class JsonlFiredProbeLedger:
    """Append-only JSONL ledger suitable for Prober subprocess wiring."""

    path: Path | str

    def _path(self) -> Path:
        return Path(self.path)

    def append(self, record: FiredProbeRecord) -> None:
        normalized = FiredProbeRecord(
            tenant_id=record.tenant_id,
            vendor_id=record.vendor_id,
            phrasing_hash=normalize_phrasing_hash(record.phrasing_hash),
            scan_id=record.scan_id,
            fired_at=record.fired_at,
            rubric_point_id=record.rubric_point_id,
            test_id=record.test_id,
        )
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(normalized.__dict__, sort_keys=True) + "\n")

    def seen_hashes(self, *, tenant_id: str, vendor_id: str) -> frozenset[str]:
        path = self._path()
        if not path.exists():
            return frozenset()
        hashes: set[str] = set()
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    row.get("tenant_id") == tenant_id
                    and row.get("vendor_id") == vendor_id
                ):
                    raw_hash = row.get("phrasing_hash")
                    if isinstance(raw_hash, str):
                        hashes.add(normalize_phrasing_hash(raw_hash))
        return frozenset(hashes)


# BEAAA-11872 — audit-spine record schema version for the fired-probe ledger.
# The book-of-record envelope is versioned independently of the sidecar module
# so a migration/loader can key on it when syncing lines into the durable
# ``canary.fired_probe_records`` Postgres table (see
# ``scanners/migrations/0001_fired_probe_ledger_v1.sql``).
FIRED_PROBE_AUDIT_SPINE_SCHEMA_VERSION = "fired_probe_ledger.v1"

_AUDIT_SPINE_PREIMAGE_FIELDS = (
    "tenant_id",
    "vendor_id",
    "phrasing_hash",
    "scan_id",
    "fired_at",
    "rubric_point_id",
    "test_id",
)


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    """Canonical UTF-8 JSON bytes — sorted keys, tight separators.

    Local to this sidecar so the anti-gaming module stays free of any
    ``determination`` import (layering: scanners must not depend on the
    determination plane). Byte-identical policy to
    ``determination.rule_dsl.canonical_json_bytes``.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _fired_probe_preimage_sha256(record: "FiredProbeRecord") -> str:
    """Tamper-evident content hash over the record's semantic fields.

    The audit-spine envelope fields (``recorded_at``, ``preimage_sha256``,
    ``record_kind``) are excluded by construction so the preimage is stable
    across replays and across the JSONL-sink and Postgres-table substrates.
    """
    preimage = {field: getattr(record, field) for field in _AUDIT_SPINE_PREIMAGE_FIELDS}
    digest = hashlib.sha256(_canonical_json_bytes(preimage)).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class AuditSpineFiredProbeLedger:
    """Durable, append-only, audit-spine-backed fired-probe ledger.

    BEAAA-11872 / Founder rule 3d. This is the *production* implementation of
    the :class:`FiredProbeLedger` Protocol — the anti-gaming canary book of
    record. It differs from :class:`JsonlFiredProbeLedger` (a test/local sink
    keyed on an arbitrary path) in three load-bearing ways:

    1. **Audit-spine semantics.** Each fired probe is written as an append-only
       book-of-record entry carrying a versioned envelope
       (``record_kind`` / ``schema_version`` / ``recorded_at``) and a
       tamper-evident ``preimage_sha256`` over the semantic fields — mirroring
       the determination audit spine (``determination/audit_spine.py``,
       ``determination/migrations/0001_determination_records_v1.sql``). Rows are
       only ever appended, never updated or deleted in place.
    2. **Durability.** The default runtime substrate is the EFS-mounted
       audit-spine sink (never ``/tmp``, never in-memory). The lines are the
       same canonical-JSON book of record the loader syncs into the durable
       ``canary.fired_probe_records`` Postgres table.
    3. **Production selection.** The production scan runtime selects this ledger
       (not ``None``, not the JSONL test ledger) whenever the customer-agent
       walker path is active — so the canary anti-gaming sidecar records in
       prod by construction rather than only when an operator sets an env var.

    Reads are scoped by both tenant and vendor, identical to the in-memory and
    JSONL implementations, so this is a drop-in for the orchestrator wiring.
    Replaying the persisted lines is idempotent for :meth:`seen_hashes` (the
    result is a set); duplicate appends are retained as book-of-record entries,
    never coalesced away.

    Whether the durable substrate is this EFS-mounted canonical-JSON audit-spine
    sink or a dedicated Postgres table is a substrate ruling deferred to
    CEO/Founder on BEAAA-11794/BEAAA-11872; both back the same append-only
    schema, so the ruling is a sink binding, not a redesign.
    """

    path: Path | str
    now_factory: Callable[[], str] = _now_iso

    def _path(self) -> Path:
        return Path(self.path)

    def append(self, record: FiredProbeRecord) -> None:
        normalized = FiredProbeRecord(
            tenant_id=record.tenant_id,
            vendor_id=record.vendor_id,
            phrasing_hash=normalize_phrasing_hash(record.phrasing_hash),
            scan_id=record.scan_id,
            fired_at=record.fired_at,
            rubric_point_id=record.rubric_point_id,
            test_id=record.test_id,
        )
        entry = {
            "record_kind": "fired_probe",
            "schema_version": FIRED_PROBE_AUDIT_SPINE_SCHEMA_VERSION,
            "recorded_at": self.now_factory(),
            "preimage_sha256": _fired_probe_preimage_sha256(normalized),
            **normalized.__dict__,
        }
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical_json_bytes(entry).decode("utf-8") + "\n")

    def seen_hashes(self, *, tenant_id: str, vendor_id: str) -> frozenset[str]:
        path = self._path()
        if not path.exists():
            return frozenset()
        hashes: set[str] = set()
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    row.get("record_kind") == "fired_probe"
                    and row.get("tenant_id") == tenant_id
                    and row.get("vendor_id") == vendor_id
                ):
                    raw_hash = row.get("phrasing_hash")
                    if isinstance(raw_hash, str):
                        hashes.add(normalize_phrasing_hash(raw_hash))
        return frozenset(hashes)

    def read_all(self) -> tuple[dict, ...]:
        """Replay every persisted audit-spine entry in append order.

        Corrupt or partial trailing lines are skipped, matching the tolerant
        reader contract of the JSONL sink.
        """
        path = self._path()
        if not path.exists():
            return ()
        rows: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return tuple(rows)


def select_fresh_canary_hashes(
    candidate_hashes: Iterable[str],
    *,
    ledger: FiredProbeLedger,
    tenant_id: str,
    vendor_id: str,
    limit: int | None = None,
) -> tuple[str, ...]:
    """Return candidate hashes never fired for this tenant/vendor.

    Candidate order is preserved and duplicates collapse to their first
    occurrence. The lookup never consults other tenants' or vendors' rows.
    """
    seen = ledger.seen_hashes(tenant_id=tenant_id, vendor_id=vendor_id)
    fresh: list[str] = []
    emitted: set[str] = set()
    for candidate in candidate_hashes:
        phrasing_hash = normalize_phrasing_hash(candidate)
        if phrasing_hash in seen or phrasing_hash in emitted:
            continue
        fresh.append(phrasing_hash)
        emitted.add(phrasing_hash)
        if limit is not None and len(fresh) >= limit:
            break
    return tuple(fresh)


@dataclass(frozen=True)
class CanaryProbeObservation:
    phrasing_hash: str
    passed: bool
    previously_fired: bool
    rubric_point_id: str | None = None
    test_id: str | None = None


@dataclass(frozen=True)
class AntiGamingCanaryResult:
    canary_flag: str = CANARY_FLAG_NONE
    evidence_ref: str | None = None
    previously_fired_pass_rate: float | None = None
    fresh_canary_pass_rate: float | None = None
    threshold: float = DEFAULT_GOODHART_THRESHOLD

    def to_dict(self) -> dict[str, object]:
        return {
            "canary_flag": self.canary_flag,
            "evidence_ref": self.evidence_ref,
            "previously_fired_pass_rate": self.previously_fired_pass_rate,
            "fresh_canary_pass_rate": self.fresh_canary_pass_rate,
            "threshold": self.threshold,
        }


def _pass_rate(observations: Sequence[CanaryProbeObservation]) -> float | None:
    if not observations:
        return None
    return sum(1 for obs in observations if obs.passed) / len(observations)


def detect_goodhart(
    observations: Iterable[CanaryProbeObservation],
    *,
    threshold: float = DEFAULT_GOODHART_THRESHOLD,
    evidence_ref: str | None = None,
) -> AntiGamingCanaryResult:
    """Flag when known phrasing pass-rate outperforms fresh canaries.

    A flag requires at least one previously-fired and one fresh observation.
    The output is an Underwriting-review signal only; callers must not apply
    it to score totals or subscores.
    """
    previous: list[CanaryProbeObservation] = []
    fresh: list[CanaryProbeObservation] = []
    for obs in observations:
        normalized = CanaryProbeObservation(
            phrasing_hash=normalize_phrasing_hash(obs.phrasing_hash),
            passed=bool(obs.passed),
            previously_fired=bool(obs.previously_fired),
            rubric_point_id=obs.rubric_point_id,
            test_id=obs.test_id,
        )
        (previous if normalized.previously_fired else fresh).append(normalized)

    previous_rate = _pass_rate(previous)
    fresh_rate = _pass_rate(fresh)
    if previous_rate is None or fresh_rate is None:
        return AntiGamingCanaryResult(
            canary_flag=CANARY_FLAG_NONE,
            evidence_ref=evidence_ref,
            previously_fired_pass_rate=previous_rate,
            fresh_canary_pass_rate=fresh_rate,
            threshold=threshold,
        )

    flag = (
        CANARY_FLAG_GOODHART_SUSPECTED
        if previous_rate - fresh_rate > threshold
        else CANARY_FLAG_NONE
    )
    return AntiGamingCanaryResult(
        canary_flag=flag,
        evidence_ref=evidence_ref if flag != CANARY_FLAG_NONE else None,
        previously_fired_pass_rate=previous_rate,
        fresh_canary_pass_rate=fresh_rate,
        threshold=threshold,
    )


def records_from_probe_metadata(
    *,
    tenant_id: str,
    vendor_id: str,
    scan_id: str,
    rows: Iterable[Mapping[str, object]],
    fired_at_factory=_now_iso,
) -> tuple[FiredProbeRecord, ...]:
    """Build ledger records from probe-side metadata rows.

    The orchestrator strips these metadata fields before IPC; they exist only
    for the anti-gaming sidecar and audit spine.
    """
    records: list[FiredProbeRecord] = []
    for row in rows:
        raw_hash = row.get("phrasing_hash") or row.get("paraphrase_hash")
        if not isinstance(raw_hash, str):
            continue
        records.append(
            FiredProbeRecord(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                phrasing_hash=normalize_phrasing_hash(raw_hash),
                scan_id=scan_id,
                fired_at=fired_at_factory(),
                rubric_point_id=(
                    str(row["rubric_point_id"])
                    if row.get("rubric_point_id") is not None
                    else None
                ),
                test_id=str(row["test_id"]) if row.get("test_id") is not None else None,
            )
        )
    return tuple(records)


def observation_from_probe_row(
    row: Mapping[str, object],
    *,
    previously_fired: bool,
) -> CanaryProbeObservation | None:
    raw_hash = row.get("phrasing_hash") or row.get("paraphrase_hash")
    if not isinstance(raw_hash, str):
        return None
    if "probe_passed" in row:
        passed = bool(row["probe_passed"])
    elif "passed" in row:
        passed = bool(row["passed"])
    else:
        raw = row.get("raw")
        passed = isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 3.0
    return CanaryProbeObservation(
        phrasing_hash=normalize_phrasing_hash(raw_hash),
        passed=passed,
        previously_fired=previously_fired,
        rubric_point_id=(
            str(row["rubric_point_id"]) if row.get("rubric_point_id") is not None else None
        ),
        test_id=str(row["test_id"]) if row.get("test_id") is not None else None,
    )


__all__ = [
    "AntiGamingCanaryResult",
    "AuditSpineFiredProbeLedger",
    "CANARY_FLAG_GOODHART_SUSPECTED",
    "CANARY_FLAG_NONE",
    "CanaryProbeObservation",
    "DEFAULT_GOODHART_THRESHOLD",
    "FIRED_PROBE_AUDIT_SPINE_SCHEMA_VERSION",
    "FiredProbeLedger",
    "FiredProbeRecord",
    "InMemoryFiredProbeLedger",
    "JsonlFiredProbeLedger",
    "detect_goodhart",
    "normalize_phrasing_hash",
    "observation_from_probe_row",
    "records_from_probe_metadata",
    "select_fresh_canary_hashes",
]
