"""Production adapter for sealed appliance contract-v1.0 batches.

The sealed appliance supplies ``verify_batch`` and its prohibited content-field
set. This adapter owns only authentication, durable ordering/idempotency, and a
counts/hashes-only Log Tracker projection.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Collection, Mapping, Any


# The appliance emits its first batch with ``batch_seq == 1``: BatchBuilder starts
# ``_seq`` at 0 and increments before assembling a body
# (backbond-appliance-0.1.0/backbond-appliance/appliance/batch.py:118). Chain state
# is durable across restarts, so the origin is 1 exactly once per appliance.
FIRST_BATCH_SEQ = 1

# The one map in contract-v1.0 whose *keys* are data values rather than field
# names. It is keyed by action_class, and "message" is both a legal action class
# and a member of the appliance's CONTENT_FIELDS, so name-matching its keys
# rejected every genuine batch (BEAAA-20237 F1). It is excluded from the
# name-based scan and checked instead by _histogram_violation, which requires it
# to be exactly the per-class tally of batch["events"] -- already scanned by name
# one step earlier. An exact-equality check leaves the map with no leak surface
# of its own: it may hold nothing that the events do not already hold.
#
# The exemption is addressed structurally, by the tuple of keys walked to reach
# the node, and never by a rendered path string. A rendered string is forgeable:
# the sender names its own keys, so a top-level key literally called
# "counts.by_action_class" renders to the same dotted path and would carry its
# whole subtree past the scan unread. Nothing else catches it -- verify_batch
# rebuilds the signed body from nine named keys, so an injected extra top-level
# key is unsigned and still verifies, and _histogram_violation reads
# batch["counts"], a different key (BEAAA-20237 F7 / BEAAA-24478 item 1). A
# tuple of keys cannot be forged by any key name, and the exemption additionally
# requires the node to be the object batch["counts"]["by_action_class"] itself.
ACTION_CLASS_HISTOGRAM_KEYS = ("counts", "by_action_class")
ACTION_CLASS_HISTOGRAM_PATH = "$.counts.by_action_class"  # rendered, for reporting only


# The nine keys the sealed verifier rebuilds the signed body from
# (backbond-appliance-0.1.0/backbond-appliance/appliance/batch.py: verify_batch,
# matching BatchBuilder._make_batch), plus the three the signer appends after
# hashing. Twelve keys, and a sealed 0.1.0 envelope carries no thirteenth.
SIGNED_BODY_FIELDS = (
    "batch_id", "appliance_id", "batch_seq", "prev_batch_hash", "window",
    "events", "determinations", "counts", "producer",
)
SEALED_ENVELOPE_FIELDS = frozenset(SIGNED_BODY_FIELDS + ("batch_hash", "signature", "key_id"))


class IngestRejected(ValueError):
    """A stable, content-free ingest rejection."""


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    duplicate: bool
    batch_hash: str


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _render_path(keys: tuple) -> str:
    """Render a walked key tuple for the rejection reason. Reporting only -- no
    control decision is ever taken on this string."""
    rendered = "$"
    for key in keys:
        if _plain_int(key):
            rendered += f"[{key}]"
        elif isinstance(key, str) and not any(char in key for char in ".[]'"):
            rendered += f".{key}"
        else:
            # A key that would otherwise render ambiguously against a genuine
            # address is bracket-quoted, so the reason names one node only.
            rendered += f"[{str(key)!r}]"
    return rendered


def _histogram_node(batch: Mapping) -> Mapping | None:
    """The one node the name scan may skip, resolved by structure up front."""
    counts = batch.get("counts")
    if isinstance(counts, Mapping):
        node = counts.get("by_action_class")
        if isinstance(node, Mapping):
            return node
    return None


def _find_prohibited(
    value: Any,
    prohibited: Collection[str],
    keys: tuple = (),
    exempt: Mapping | None = None,
) -> str | None:
    """Name-scan every node for a prohibited field.

    ``exempt`` is the action-class histogram resolved by ``_histogram_node``. It
    is skipped only where both the walked key tuple and object identity say this
    is that node; passing nothing exempts nothing, so a caller that forgets the
    argument fails closed on genuine batches rather than opening the boundary.
    """
    if isinstance(value, Mapping):
        if keys == ACTION_CLASS_HISTOGRAM_KEYS and exempt is not None and value is exempt:
            return None  # value-keyed; checked by _histogram_violation instead
        for key, child in value.items():
            child_keys = (*keys, key)
            if key in prohibited:
                return _render_path(child_keys)
            found = _find_prohibited(child, prohibited, child_keys, exempt)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_prohibited(child, prohibited, (*keys, index), exempt)
            if found:
                return found
    return None


def _histogram_violation(batch: Mapping) -> str | None:
    """The action-class histogram must be exactly the tally of this batch's events.

    Run only after the name-based scan has cleared ``batch["events"]``. Exact
    equality means every key is an action_class already carried by a scanned
    event and every value is a count this adapter derived itself, so the map
    cannot smuggle a byte the events did not already carry.
    """
    counts = batch.get("counts")
    if not isinstance(counts, Mapping) or "by_action_class" not in counts:
        return None
    histogram = counts["by_action_class"]
    if not isinstance(histogram, Mapping):
        return ACTION_CLASS_HISTOGRAM_PATH
    expected: dict[Any, int] = {}
    events = batch.get("events")
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, Mapping):
            return ACTION_CLASS_HISTOGRAM_PATH
        action_class = event.get("action_class")
        if not isinstance(action_class, str):
            return ACTION_CLASS_HISTOGRAM_PATH
        expected[action_class] = expected.get(action_class, 0) + 1
    if dict(histogram) != expected:
        return ACTION_CLASS_HISTOGRAM_PATH
    return None


def _unknown_envelope_field(batch: Mapping) -> bool:
    """True when the envelope carries a key no sealed 0.1.0 batch has.

    A set comparison against the pinned allowlist, never a denylist: the residual
    this closes is precisely the field nobody thought to name. ``verify_batch``
    rebuilds the signed body from nine named keys, so any extra top-level key is
    outside both the signature and ``batch_hash`` and still verifies. Today it is
    caught only if its own name, or a name in its subtree, is in the sealed
    CONTENT_FIELDS -- an ``evidence_tier`` rider is in neither and rides through
    (BEAAA-24623 ruling 1). Inert-because-no-consumer-exists is not a control.
    """
    return bool(set(batch) - SEALED_ENVELOPE_FIELDS)


def _require_seq(value: Any) -> int:
    if not _plain_int(value) or value < FIRST_BATCH_SEQ:
        raise IngestRejected("malformed_batch_seq")
    return value


def _require_count(counts: Mapping, key: str) -> int:
    value = counts.get(key, 0)
    if not _plain_int(value) or value < 0:
        raise IngestRejected(f"malformed_counts:{key}")
    return value


# ---------------------------------------------------------------------------
# P1 observability surface (BEAAA-24623 ruling 4).
#
# /healthz + /metrics as they stood is not the surface: a tenant that has
# delivered nothing and a tenant whose every delivery is rejected returned
# identical counters, so the failure mode the pilot is most likely to hit was
# the one nothing surfaced. Two additions close that -- a structured line per
# request outcome on stderr, which journald already carries under the existing
# unit, and rejection/duplicate/outage counters plus the chain tip on /metrics.
# Paging, alert routing, scraping and retention stay out of P1.
# ---------------------------------------------------------------------------

INGEST_OUTCOMES = ("accepted", "duplicate", "rejected", "unavailable")

# Identifier alphabet an enrolled appliance_id is drawn from. The value in a log
# line comes off the wire, and the sender names it, so it is bounded to this
# before it is written: JSON encoding already stops a forged newline from
# splitting one record into two, and the bound stops the field being used as a
# free-text channel into the operator's log the way a rendered path would be.
_SAFE_APPLIANCE_ID = re.compile(r"\A[A-Za-z0-9._:-]{1,64}\Z")


def reason_class(reason: str | None) -> str | None:
    """The class of a rejection reason, never its rendered detail.

    ``content_boundary_violated:$.a.b`` embeds keys the sender named, so the
    suffix is dropped before the reason reaches a log line or a counter label.
    """
    if reason is None:
        return None
    return reason.split(":", 1)[0]


def _safe_appliance_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_APPLIANCE_ID.match(value) else None


def outcome_log_line(
    *,
    outcome: str,
    reason: str | None = None,
    appliance_id: Any = None,
    batch_seq: Any = None,
    now: datetime | None = None,
) -> str:
    """One structured line per request outcome, for stderr and thence journald.

    Carries an ISO-8601 UTC timestamp, the outcome, the reason *class*,
    ``appliance_id`` and ``batch_seq`` where known. ``tenant_id`` never appears,
    in clear or otherwise: the appliance_id is bound to exactly one tenant by
    enrollment, so it is the identifier an operator needs and the tenant stays
    off the log.
    """
    if outcome not in INGEST_OUTCOMES:
        raise ValueError("unknown ingest outcome")
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    record = {
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z",
        "event": "appliance_ingest",
        "outcome": outcome,
        "reason_class": reason_class(reason),
        "appliance_id": _safe_appliance_id(appliance_id),
        "batch_seq": batch_seq if _plain_int(batch_seq) else None,
    }
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


class OutcomeCounters:
    """Process-local, per-tenant request-outcome tallies.

    Process-local is deliberate for P1: journald and the append-only receipt
    table are the durable record, so these need no migration and are expected to
    reset with the unit. They are keyed by the tenant the request *claimed*,
    which is the only tenant known when a delivery is refused before enrollment
    resolves; reading them still requires that tenant's enrolled token, so an
    attempt naming a tenant it cannot authenticate as is visible to that tenant
    and to nobody else.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenants: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"rejected_total": {}, "duplicates_total": 0, "unavailable_total": 0,
                "accepted_total": 0}

    def record(self, tenant_id_hash: str, outcome: str, reason: str | None = None) -> None:
        if outcome not in INGEST_OUTCOMES:
            raise ValueError("unknown ingest outcome")
        with self._lock:
            tally = self._tenants.setdefault(tenant_id_hash, self._empty())
            if outcome == "rejected":
                label = reason_class(reason) or "unclassified"
                tally["rejected_total"][label] = tally["rejected_total"].get(label, 0) + 1
            elif outcome == "duplicate":
                tally["duplicates_total"] += 1
            elif outcome == "unavailable":
                tally["unavailable_total"] += 1
            else:
                tally["accepted_total"] += 1

    def snapshot(self, tenant_id_hash: str) -> dict[str, Any]:
        with self._lock:
            tally = self._tenants.get(tenant_id_hash) or self._empty()
            return {**tally, "rejected_total": dict(tally["rejected_total"])}


class ApplianceIngestStore:
    """Atomic SQLite adapter; verification always precedes duplicate handling."""

    def __init__(
        self,
        database: str | Path,
        migration: str | Path,
        *,
        verify_batch: Callable[[dict, str], bool],
        prohibited_fields: Collection[str],
    ) -> None:
        self.database = str(database)
        self.verify_batch = verify_batch
        self.prohibited_fields = frozenset(prohibited_fields)
        self.counters = OutcomeCounters()
        with self._connect() as db:
            db.executescript(Path(migration).read_text(encoding="utf-8"))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def enroll(
        self, *, appliance_id: str, tenant_id: str, source_id: str,
        token: str, public_key_pem: str,
    ) -> None:
        if not all((appliance_id, tenant_id, source_id, token, public_key_pem)):
            raise ValueError("complete enrollment required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = (_digest(tenant_id), _digest(source_id), _digest(token), public_key_pem)
            current = db.execute(
                "SELECT tenant_id_hash,source_id_hash,token_hash,public_key_pem "
                "FROM appliance_enrollments WHERE appliance_id=?", (appliance_id,),
            ).fetchone()
            if current is None:
                db.execute(
                    "INSERT INTO appliance_enrollments VALUES (?,?,?,?,?,"
                    "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (appliance_id, *values),
                )
            elif current != values:
                raise ValueError("enrollment_binding_conflict")
            db.commit()

    def ingest(self, batch: dict, *, token: str, tenant_id: str, source_id: str) -> IngestResult:
        appliance_id = str(batch.get("appliance_id", ""))
        batch_hash = str(batch.get("batch_hash", ""))
        with self._connect() as db:
            enrollment = db.execute(
                "SELECT tenant_id_hash,source_id_hash,token_hash,public_key_pem "
                "FROM appliance_enrollments WHERE appliance_id=?", (appliance_id,),
            ).fetchone()
        if enrollment is None:
            raise IngestRejected("unknown_appliance")
        tenant_hash, source_hash, token_hash, public_key = enrollment
        if not (
            hmac.compare_digest(token_hash, _digest(token))
            and hmac.compare_digest(tenant_hash, _digest(tenant_id))
            and hmac.compare_digest(source_hash, _digest(source_id))
        ):
            raise IngestRejected("unauthorized_scope")

        # Sealed contract verifier recomputes the canonical batch hash and checks
        # the Ed25519 signature. This must happen before the duplicate lookup.
        if not self.verify_batch(batch, public_key):
            raise IngestRejected("signature_or_batch_hash_invalid")

        leaked_path = _find_prohibited(
            batch, self.prohibited_fields, exempt=_histogram_node(batch)
        )
        if leaked_path is None:
            leaked_path = _histogram_violation(batch)
        if leaked_path:
            raise IngestRejected(f"content_boundary_violated:{leaked_path}")

        # Ordered after the content scan deliberately. Both checks refuse the same
        # delivery, so the order decides only which reason class an injected key
        # reports under, and a key whose subtree carries prohibited content is
        # better reported as the leak it is; that also keeps the BEAAA-24478
        # item 1 forgery regressions reporting content_boundary_violated. The
        # reason names no field: the sender chose that key, and a sender-chosen
        # string echoed into a rejection is the same content channel the rendered
        # path was (BEAAA-24623 ruling 4).
        if _unknown_envelope_field(batch):
            raise IngestRejected("unknown_envelope_field")

        events = batch.get("events", [])
        determinations = batch.get("determinations", [])
        for item in [*events, *determinations]:
            if item.get("tenant_id") != tenant_id:
                raise IngestRejected("unauthorized_scope")
        counts = batch.get("counts") or {}
        # Malformed shape is a deterministic 422, never a retryable 503 (F6).
        batch_seq = _require_seq(batch.get("batch_seq"))
        receipt = (
            batch_hash, appliance_id, batch_seq,
            batch.get("prev_batch_hash"), tenant_hash, source_hash,
            len(events), len(determinations),
            _require_count(counts, "billable_actions"), _require_count(counts, "tokens"),
        )

        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                if db.execute(
                    "SELECT 1 FROM appliance_ingest_receipts WHERE batch_hash=?",
                    (batch_hash,),
                ).fetchone():
                    db.rollback()
                    return IngestResult(True, True, batch_hash)
                tip = db.execute(
                    "SELECT batch_hash,batch_seq FROM appliance_ingest_receipts "
                    "WHERE appliance_id=? ORDER BY batch_seq DESC LIMIT 1",
                    (appliance_id,),
                ).fetchone()
                expected_prev = tip[0] if tip else None
                expected_seq = tip[1] + 1 if tip else FIRST_BATCH_SEQ
                if batch.get("prev_batch_hash") != expected_prev or batch_seq != expected_seq:
                    raise IngestRejected("broken_chain")
                db.execute(
                    "INSERT INTO appliance_ingest_receipts "
                    "(batch_hash,appliance_id,batch_seq,prev_batch_hash,tenant_id_hash,"
                    "source_id_hash,events_count,determinations_count,billable_actions,tokens_count) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    receipt,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return IngestResult(True, False, batch_hash)

    def record_outcome(self, *, tenant_id: str, outcome: str, reason: str | None = None) -> None:
        """Tally one request outcome against the tenant the request claimed."""
        self.counters.record(_digest(tenant_id), outcome, reason)

    def _chain_tips(self, tenant_id_hash: str) -> dict[str, int]:
        """Highest accepted batch_seq per appliance, for this tenant only.

        The durable half of the surface: counters reset with the process, this
        does not, so an operator can tell a quiet appliance from a stalled one
        after a restart.
        """
        with self._connect() as db:
            rows = db.execute(
                "SELECT appliance_id,MAX(batch_seq) FROM appliance_ingest_receipts "
                "WHERE tenant_id_hash=? GROUP BY appliance_id",
                (tenant_id_hash,),
            ).fetchall()
        return {appliance_id: seq for appliance_id, seq in rows}

    def status(self, *, tenant_id_hash: str | None = None) -> dict[str, int]:
        """Unscoped by default; not reachable over HTTP. See scoped_status."""
        sql = ("SELECT COUNT(*),COALESCE(SUM(events_count),0),"
               "COALESCE(SUM(determinations_count),0) FROM appliance_ingest_receipts")
        params: tuple = ()
        if tenant_id_hash is not None:
            sql += " WHERE tenant_id_hash=?"
            params = (tenant_id_hash,)
        with self._connect() as db:
            batches, events, determinations = db.execute(sql, params).fetchone()
        return {"batches_accepted": batches, "events_accepted": events,
                "determinations_accepted": determinations}

    def scoped_status(self, *, token: str, tenant_id: str) -> dict[str, Any]:
        """Authenticated, single-tenant counts. The only status path on the wire (F5)."""
        tenant_hash = _digest(tenant_id)
        expected = _digest(token)
        with self._connect() as db:
            rows = db.execute(
                "SELECT token_hash FROM appliance_enrollments WHERE tenant_id_hash=?",
                (tenant_hash,),
            ).fetchall()
        if not any(hmac.compare_digest(row[0], expected) for row in rows):
            raise IngestRejected("unauthorized_scope")
        return {
            **self.status(tenant_id_hash=tenant_hash),
            **self.counters.snapshot(tenant_hash),
            "chain_tip_batch_seq": self._chain_tips(tenant_hash),
        }
