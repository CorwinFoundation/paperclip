"""Single fail-closed activation check for consumer protection status.

Every surface that can state protection status - screen, receipt, API response,
referral page - reads from :func:`check_activation`. No surface may infer
coverage from a scan result, a receipt, a synthetic policy id, or a
subscription charge, so no function here accepts any of those as input.

Fail closed: a missing, malformed, or expired record is not-active, and an
error anywhere in the check is not-active. There is no path that returns
active on an exception.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

NO_COVERAGE_STATEMENT = (
    "Demo or observation only. No protection, reimbursement or coverage is active."
)

ACTIVE_STATEMENT_TEMPLATE = (
    "Protection is active under approved terms {terms_version} "
    "for program version {program_version}."
)

# The free period starts on an activation record and on nothing else.
FREE_PERIOD_TRIGGER = "activation_record"


@dataclass(frozen=True)
class RecordRequirement:
    key: str
    required_status: str
    required_fields: tuple[str, ...]
    expiry_required: bool = False


REQUIRED_RECORDS: tuple[RecordRequirement, ...] = (
    RecordRequirement(
        "governing_legal_structure",
        "in_good_standing",
        ("entity_name", "jurisdiction"),
    ),
    RecordRequirement(
        "executed_backing_authority",
        "executed",
        ("counterparty", "executed_at", "document_sha256"),
    ),
    RecordRequirement(
        "funded_financial_security",
        "funded",
        ("instrument", "funded_amount_cents", "funded_at"),
    ),
    RecordRequirement(
        "approved_versioned_terms",
        "approved",
        ("terms_version", "approved_at"),
        expiry_required=True,
    ),
    RecordRequirement(
        "named_claims_and_payment_humans",
        "named",
        ("claims_reviewer", "payment_approver"),
    ),
    RecordRequirement(
        "approved_consumer_disclosure_set",
        "approved",
        ("disclosure_set_id", "approved_at"),
    ),
)

REQUIRED_RECORD_KEYS: tuple[str, ...] = tuple(r.key for r in REQUIRED_RECORDS)


@dataclass(frozen=True)
class ActivationStatus:
    """The only authority on whether protection is active."""

    active: bool
    program_version: str
    statement: str
    missing_records: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    activation_record_id: str | None = None
    terms_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "protection_active": self.active,
            "program_version": self.program_version,
            "statement": self.statement,
            "missing_records": list(self.missing_records),
            "reasons": list(self.reasons),
            "activation_record_id": self.activation_record_id,
            "terms_version": self.terms_version,
        }


class RecordRegistry:
    """Source of activation records. Implementations must not fail open."""

    def records(self, program_version: str) -> Mapping[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class MappingRegistry(RecordRegistry):
    by_version: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def records(self, program_version: str) -> Mapping[str, Any]:
        return self.by_version.get(program_version, {})


@dataclass(frozen=True)
class FileRegistry(RecordRegistry):
    """Reads ``<root>/<program_version>.json``. Absent file means not active."""

    root: str

    def records(self, program_version: str) -> Mapping[str, Any]:
        path = Path(self.root) / f"{program_version}.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text())


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _check_record(
    requirement: RecordRequirement,
    record: Any,
    program_version: str,
    now: datetime,
) -> list[str]:
    """Return reason codes. Empty list means the record satisfies the gate."""
    key = requirement.key
    if record is None:
        return [f"{key}:missing"]
    if not isinstance(record, Mapping):
        return [f"{key}:malformed"]

    reasons: list[str] = []
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        reasons.append(f"{key}:malformed")
    if record.get("program_version") != program_version:
        reasons.append(f"{key}:program_version_mismatch")
    if record.get("status") != requirement.required_status:
        reasons.append(f"{key}:status_not_{requirement.required_status}")
    for name in requirement.required_fields:
        value = record.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            reasons.append(f"{key}:missing_field:{name}")

    amount = record.get("funded_amount_cents")
    if key == "funded_financial_security":
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            reasons.append(f"{key}:not_funded")

    expires_at = record.get("expires_at")
    if expires_at is None:
        if requirement.expiry_required:
            reasons.append(f"{key}:missing_field:expires_at")
    else:
        parsed = _parse_timestamp(expires_at)
        if parsed is None:
            reasons.append(f"{key}:malformed_expiry")
        elif parsed <= now:
            reasons.append(f"{key}:expired")
    return reasons


def _not_active(
    program_version: str,
    *,
    missing: Iterable[str],
    reasons: Iterable[str],
) -> ActivationStatus:
    return ActivationStatus(
        active=False,
        program_version=program_version,
        statement=NO_COVERAGE_STATEMENT,
        missing_records=tuple(missing),
        reasons=tuple(reasons),
    )


def unrecorded_protection_state(
    surface: str, *, reason: str = "protection_state_not_recorded"
) -> dict[str, Any]:
    """Protection block for a surface that has no recorded activation state.

    A receipt written before activation state was persisted with the case has no
    evidence of coverage, and absence of evidence is never coverage: this always
    reads as not active. BEAAA-35133 defect A.
    """
    block = _not_active(
        "unrecorded", missing=REQUIRED_RECORD_KEYS, reasons=(reason,)
    ).as_dict()
    block["surface"] = surface
    return block


def _activation_record_id(program_version: str, records: Mapping[str, Any]) -> str:
    material = json.dumps(
        {key: records[key].get("record_id") for key in REQUIRED_RECORD_KEYS},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(f"{program_version}:{material}".encode()).hexdigest()
    return f"activation-{digest[:16]}"


def check_activation(
    program_version: str,
    registry: RecordRegistry | None,
    *,
    now: datetime | None = None,
) -> ActivationStatus:
    """Return protection status. Never raises; any error means not active."""
    try:
        if not isinstance(program_version, str) or not program_version.strip():
            return _not_active(
                str(program_version),
                missing=REQUIRED_RECORD_KEYS,
                reasons=("program_version:malformed",),
            )
        if registry is None:
            return _not_active(
                program_version,
                missing=REQUIRED_RECORD_KEYS,
                reasons=("registry:absent",),
            )

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)

        records = registry.records(program_version)
        if not isinstance(records, Mapping):
            return _not_active(
                program_version,
                missing=REQUIRED_RECORD_KEYS,
                reasons=("registry:malformed",),
            )

        missing: list[str] = []
        reasons: list[str] = []
        for requirement in REQUIRED_RECORDS:
            record_reasons = _check_record(
                requirement, records.get(requirement.key), program_version, moment
            )
            if record_reasons:
                missing.append(requirement.key)
                reasons.extend(record_reasons)

        if missing:
            return _not_active(program_version, missing=missing, reasons=reasons)

        terms_version = str(records["approved_versioned_terms"]["terms_version"])
        return ActivationStatus(
            active=True,
            program_version=program_version,
            statement=ACTIVE_STATEMENT_TEMPLATE.format(
                terms_version=terms_version, program_version=program_version
            ),
            activation_record_id=_activation_record_id(program_version, records),
            terms_version=terms_version,
        )
    except Exception:  # noqa: BLE001 - fail closed on anything at all
        return _not_active(
            program_version if isinstance(program_version, str) else str(program_version),
            missing=REQUIRED_RECORD_KEYS,
            reasons=("activation_check_error",),
        )


def surface_protection_state(
    surface: str,
    program_version: str,
    registry: RecordRegistry | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Protection block for any surface.

    Takes no scan result, receipt, policy id, or subscription argument, so a
    surface cannot infer coverage from one.
    """
    status = check_activation(program_version, registry, now=now)
    block = status.as_dict()
    block["surface"] = surface
    return block


def referral_disclosure(status: ActivationStatus) -> str:
    """Referral attribution copy. Must not imply coverage exists."""
    if not status.active:
        return NO_COVERAGE_STATEMENT
    return status.statement


@dataclass(frozen=True)
class FreePeriod:
    activation_record_id: str
    program_version: str
    started_at: datetime
    days: int


def start_free_period(
    status: ActivationStatus,
    event_kind: str,
    *,
    started_at: datetime | None = None,
    days: int = 30,
) -> FreePeriod | None:
    """Start the free period only on an activation record.

    Interest-list signup, a failed connection, and a demo completion are not
    activation records and return ``None``. ``days`` is an illustrative
    planning input, not an approved benefit.
    """
    if event_kind != FREE_PERIOD_TRIGGER:
        return None
    if not status.active or not status.activation_record_id:
        return None
    return FreePeriod(
        activation_record_id=status.activation_record_id,
        program_version=status.program_version,
        started_at=started_at or datetime.now(timezone.utc),
        days=days,
    )


DEFAULT_PROGRAM_VERSION = "stage-a-demo"
ACTIVATION_RECORDS_ENV = "BACKBOND_ACTIVATION_RECORDS"
PROGRAM_VERSION_ENV = "BACKBOND_PROGRAM_VERSION"


def default_program_version() -> str:
    import os

    return os.environ.get(PROGRAM_VERSION_ENV) or DEFAULT_PROGRAM_VERSION


def default_registry() -> RecordRegistry:
    """Registry for the running demo. No records directory means not active."""
    import os

    return FileRegistry(os.environ.get(ACTIVATION_RECORDS_ENV) or "config/activation-records")


def current_status(*, now: datetime | None = None) -> ActivationStatus:
    return check_activation(default_program_version(), default_registry(), now=now)
