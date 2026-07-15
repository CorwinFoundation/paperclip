"""Live-probe worker process model — BEAAA-542 / ADR-0009 §5 #4.

Defensive control implementing the canonical §5 #4 build line from
``docs/adr/adr-0009-live-agent-hosted-probe-v0.md``:

    Scan worker process model — run Prober in ``multiprocessing.Process``
    with restricted IPC channel to Scorer worker; channel only carries
    JSON-serializable measurement rows + evidence URIs.

This module ships the **surface**: it does not yet wire into the
``POST /scan`` route. ADR-0009 §5 #2 (the orchestrator) is the sibling
ticket that imports both this module and the credential vault. Per the
rollback note on BEAAA-542, this file can be deleted in isolation
without touching any other code path until the orchestrator lands.

Hard Invariants enforced
------------------------

* **#3 (Validator wrap, Δ1).** ``validate_inbound`` calls
  :func:`api.intake_guard.enforce_hosted_eval_only` and
  :func:`api.intake_guard.enforce_input_safety` verbatim. We do not
  re-implement the rules — they are imported and invoked. The Auditor
  checks for duplicate-logic drift on every release.
* **#4 (Credential isolation).** The IPC payload schema is a closed
  allow-list of fields. Credentials are NOT in that allow-list, so the
  vault token cannot be smuggled across the boundary as an extra field.
  ``serialize_measurement_row`` rejects any extra key, any non-JSON
  type, any ``bytes`` / ``bytearray`` / object reference, and any
  ``CredentialVault`` / ``CredentialRef`` instance.
* **#5 (Tamper resistance posture).** The Scorer process opens
  :class:`harness.egress.ScopedEgressAllowlist` with ``[]`` (deny-all),
  so any outbound HTTP attempt from the Scorer raises
  :class:`harness.egress.EgressNotAllowed` at the transport layer.

IPC channel
-----------

The channel is a ``multiprocessing.Queue`` carrying dicts produced by
:func:`serialize_measurement_row`. Each message is one canonical
``eval_outputs`` row keyed by ``dim_id``. The Scorer accumulates them
into a single ``eval_outputs`` dict and runs the verbatim intake-guard
validators before handing the dict to the scoring engine.

The Prober opens its own ``ScopedEgressAllowlist(agent_endpoint.host)``
scope. The Scorer opens ``ScopedEgressAllowlist([])``. Both scopes
live for the entire worker lifetime.

Process model
-------------

``multiprocessing.get_context("fork")`` is used so the API-edge-side
:class:`~harness.credentials.CredentialVault` is inherited into the
Prober process via copy-on-write memory (not via pickle / IPC). This
preserves the documented vault contract — the secret is never
re-serialized to cross a process boundary. The vault's ``__reduce__``
already refuses to pickle the secret, so even if a future call site
mistakenly tried to pass a vault through the IPC queue, the receiver
would get a detached vault that resolves to ``None`` for every ref.

Anti-pattern guard
------------------

If you find yourself tempted to copy lines out of ``api.intake_guard``
into this module so the Scorer "validates locally" — stop. That is
exactly the duplicate-logic drift the Auditor checks for. Import and
call the upstream functions. If their behavior needs to change, the
change lands in ``api.intake_guard`` and both call sites pick it up
on the same merge.
"""
from __future__ import annotations

import multiprocessing as mp
import multiprocessing.context as mp_context
import multiprocessing.queues as mp_queues
import os
import queue
import sys
from typing import Any, Callable, Iterable

# Δ1 — verbatim wrap of the existing API-edge intake guard. Do NOT
# re-implement the rules here. The Auditor flags duplicate-logic drift.
from api.intake_guard import (
    IntakeError,
    enforce_hosted_eval_only,
    enforce_input_safety,
)
from harness.egress import EgressNotAllowed, ScopedEgressAllowlist

# BEAAA-14422 / code-review F3 — shared per-dim K-replicate median. The
# Scorer scores the persisted envelope from the SAME aggregation the
# orchestrator uses for ``ScanResult.dim_results``, so a nondeterministic
# agent cannot produce two different ``are_total`` on the two authoritative
# surfaces. ``_median_of`` is imported from the dependency-free leaf module
# ``scanners.replicate_median`` (NOT from ``scanners.live_probe_orchestrator``,
# which would pull ``harness.credentials`` across the Scorer-side
# import-isolation boundary — Hard Invariant #4). Do NOT reimplement a
# second median: the orchestrator imports the SAME function, so the two
# surfaces are byte-identical BY CONSTRUCTION.
from scanners.replicate_median import _median_of


# BEAAA-7837 — scorer-side row drain ceiling. The customer runner's
# per-request timeout is 30s, so 60s gives a slow probe one full request
# window plus slack while still preventing an unbounded recv_row hang when
# the Prober dies or never sends END_OF_SCAN.
DEFAULT_SCORER_ROW_RECV_TIMEOUT_SECONDS: float = 60.0


# ---------------------------------------------------------------------------
# IPC schema — closed allow-list of fields, Hard Invariant #4 backstop.
# ---------------------------------------------------------------------------

# Allow-list of fields permitted in a single measurement row sent over
# the IPC queue. Restricting to this set means no opaque-bytes / opaque-
# string field exists that could carry a credential. Every field in the
# list is also validated by ``enforce_input_safety`` on the Scorer side
# (Hard Invariant #3).
#
# This set is intentionally the strict union of:
#   (a) intake_guard.enforce_hosted_eval_only's required fields
#       (``runner``, ``eval_run_id``)
#   (b) intake_guard.enforce_input_safety's validated fields
#       (``raw``, ``evidence_uri``, ``evidence_sha256``, ``runner``,
#       ``eval_run_id``)
# plus the ``dim_id`` key carried alongside the row for routing on the
# Scorer side. No free-text trace, no raw probe response body, no
# benchmark metadata. Anything else lives in the evidence bundle behind
# the ``evidence_uri``; the Scorer never dereferences it.
ALLOWED_ROW_FIELDS: frozenset[str] = frozenset({
    "dim_id",
    "raw",
    "runner",
    "eval_run_id",
    "evidence_uri",
    "evidence_sha256",
})

# Hard cap on serialized row bytes (DoS surface — the Prober's target is
# adversarial). 64 KiB is comfortably above legitimate rows (sub-1 KiB)
# and well below "this could starve the queue".
MAX_IPC_ROW_BYTES: int = 64 * 1024

# Sentinel value pushed by the Prober when the probe corpus is exhausted
# so the Scorer's drain loop terminates cleanly. Not a row, not a dict —
# a plain string the schema validator rejects up front.
END_OF_SCAN: str = "__END_OF_SCAN__"


# BEAAA-2182 Track A — closed allow-list of fields permitted in the
# Scorer→API IPC envelope return channel. Mirrors :data:`ALLOWED_ROW_FIELDS`
# pattern: frozenset, validated on both serialize and receive sides, no
# free-text trace, no opaque bytes (every leaf is JSON-primitive per
# :func:`_is_pure_json_value`).
#
# Set = ADR-0017 addendum-1 §2.2 envelope fields + the optional cap
# sentinel ``prior_scan_refs_truncated`` (per §7 Q2). The same dict the
# Scorer-side :func:`scanners.score_runtime.ScoreRuntime.compute_envelope`
# returns is checked against this allow-list before it crosses the wire,
# and re-checked on receive by the API-edge parent (defense in depth —
# Hard Invariant #4 not contingent on a well-behaved Scorer).
ALLOWED_SCORE_ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "agent_id",
    "are_total",
    "band",
    "confidence_below_adjudication_threshold",
    "corpus_version",
    "evidence_refs",
    "model_versions",
    "partial_completion",
    "prior_scan_refs",
    "prior_scan_refs_truncated",
    "purpose",
    "rubric_version",
    "run_timestamp",
    "scanner_version",
    "seed",
    "subscores",
})

# Hard cap on the serialized envelope bytes. Larger than MAX_IPC_ROW_BYTES
# because ``evidence_refs`` carries one entry per active rubric dim (≥30
# dims at v1.0.1 rubric), each ~256 bytes — empirically <16 KiB even at
# full coverage. 256 KiB ceiling leaves headroom for future N/A
# justification growth and stays well below queue-starvation territory.
MAX_IPC_ENVELOPE_BYTES: int = 256 * 1024


class IPCSchemaError(ValueError):
    """Outgoing IPC payload failed the closed allow-list / type check.

    Raised by :func:`serialize_measurement_row` BEFORE the row goes on
    the wire — Hard Invariant #4. Distinct from ``IntakeError`` (raised
    by the Scorer-side validator on inbound messages).
    """


# Types that must never appear in an IPC payload. ``bytes`` / ``bytearray``
# would be opaque enough to smuggle a credential; ``memoryview`` exposes
# the underlying buffer; arbitrary objects with ``__reduce__`` would
# carry pickle-graph escape paths.
_FORBIDDEN_VALUE_TYPES: tuple[type, ...] = (
    bytes, bytearray, memoryview,
)


def _is_pure_json_value(value: Any) -> bool:
    """True if ``value`` is a JSON-primitive (or nested all-JSON container).

    The IPC schema requires every leaf to be a JSON primitive. This is
    intentionally stricter than ``json.dumps(value)`` accepting it: we
    reject ``set`` / ``tuple`` / dataclass / custom objects even though
    some of them could be coerced, because the goal is "the receiver sees
    exactly what was declared, nothing more". The receiver never has to
    guess what an unknown type was meant to be.
    """
    if value is None or isinstance(value, (bool, int, float)):
        # ``bool`` is intentionally allowed at the type-check layer; the
        # intake-guard rejects bool-as-raw separately (raw must be int /
        # float and explicitly not bool). Other fields may legitimately
        # be bool in future, so the IPC schema does not blanket-reject.
        return True
    if isinstance(value, str):
        return True
    if isinstance(value, list):
        return all(_is_pure_json_value(v) for v in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _is_pure_json_value(v)
            for k, v in value.items()
        )
    return False


def serialize_measurement_row(
    dim_id: str, row: dict[str, Any]
) -> dict[str, Any]:
    """Prober-side. Validate and return a sendable row dict.

    Raises :class:`IPCSchemaError` if the row carries any field outside
    :data:`ALLOWED_ROW_FIELDS`, any value whose type is not a JSON
    primitive, any ``bytes`` / ``bytearray`` / opaque object, or any
    instance of :class:`harness.credentials.CredentialVault` or
    :class:`~harness.credentials.CredentialRef`.

    The returned dict is what should be put on the IPC queue. The Scorer
    side calls :func:`validate_inbound` on it before reaching the
    scoring engine.
    """
    if not isinstance(row, dict):
        raise IPCSchemaError(
            f"row must be a dict, got {type(row).__name__}"
        )

    # Compose the candidate row with dim_id baked in so the schema check
    # is uniform — every key, including ``dim_id``, is in the allow-list.
    if not isinstance(dim_id, str) or not dim_id:
        raise IPCSchemaError("dim_id must be a non-empty str")
    if "dim_id" in row and row["dim_id"] != dim_id:
        raise IPCSchemaError(
            f"row['dim_id']={row['dim_id']!r} disagrees with positional "
            f"dim_id={dim_id!r}"
        )
    candidate: dict[str, Any] = {"dim_id": dim_id}
    for k, v in row.items():
        if k == "dim_id":
            continue
        candidate[k] = v

    extra = set(candidate) - ALLOWED_ROW_FIELDS
    if extra:
        raise IPCSchemaError(
            f"row contains fields not in the IPC allow-list: "
            f"{sorted(extra)}. Allowed: {sorted(ALLOWED_ROW_FIELDS)}. "
            "Anything not in this list is rejected before transit "
            "(Hard Invariant #4)."
        )

    for k, v in candidate.items():
        if isinstance(v, _FORBIDDEN_VALUE_TYPES):
            raise IPCSchemaError(
                f"{k!r}: {type(v).__name__} is forbidden in IPC payloads "
                "(opaque bytes could smuggle a credential)."
            )
        # Catch CredentialVault / CredentialRef by structural inspection so
        # this module does NOT import ``harness.credentials`` (which would
        # break the Scorer-side import-isolation invariant verified by
        # tests/test_credentials_beaaa538.py).
        cls_name = type(v).__name__
        if cls_name in ("CredentialVault", "CredentialRef"):
            raise IPCSchemaError(
                f"{k!r}: {cls_name} instances must never cross the IPC "
                "boundary (Hard Invariant #4)."
            )
        if not _is_pure_json_value(v):
            raise IPCSchemaError(
                f"{k!r}: value of type {type(v).__name__} is not a JSON "
                "primitive; IPC payloads must be JSON-pure."
            )

    # DoS cap: serialized payload size. ``json.dumps`` is used as a
    # cheap byte-length proxy; the actual queue transport is pickle but
    # the JSON-pure schema makes the two sizes within a small constant.
    import json
    encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_IPC_ROW_BYTES:
        raise IPCSchemaError(
            f"serialized row exceeds {MAX_IPC_ROW_BYTES} bytes "
            f"({len(encoded)} chars) — refusing to enqueue."
        )

    return candidate


def validate_inbound(message: Any) -> tuple[str, dict[str, Any]]:
    """Scorer-side. Validate an IPC message and return ``(dim_id, entry)``.

    Runs the verbatim ``api.intake_guard`` validators on a single-entry
    ``eval_outputs`` map constructed from the message:

    1. :func:`api.intake_guard.enforce_hosted_eval_only` — provenance:
       runner must be ``"beaai-hosted-eval"``, eval_run_id must be a
       UUID.
    2. :func:`api.intake_guard.enforce_input_safety` — shape-safety:
       dim_id matches ``Pn.m``, raw is numeric (not bool), evidence_uri
       is ``https://`` (or empty), evidence_sha256 matches
       ``sha256:<64-hex>`` (or empty), no control characters / DEL in
       string fields, no oversize values.

    Raises :class:`api.intake_guard.IntakeError` on the first failed
    rule — the message is dropped and never reaches the scoring engine.

    The validator is the LAST line of defense before scoring; the
    sending side's :func:`serialize_measurement_row` is the FIRST. Both
    layers are required: serialize-side guards against credential
    smuggling, validate-side guards against an attacker who somehow
    bypassed the schema (e.g. a future caller forgot to use the
    serializer).
    """
    if message == END_OF_SCAN:
        raise IPCSchemaError(
            "validate_inbound called on END_OF_SCAN sentinel — caller "
            "should check for the sentinel before invoking the validator."
        )
    if not isinstance(message, dict):
        raise IntakeError(
            f"IPC message must be a dict, got {type(message).__name__}"
        )

    extra = set(message) - ALLOWED_ROW_FIELDS
    if extra:
        # Defense in depth: even though the sender's serializer rejects
        # extras, the receiver re-checks. Hard Invariant #4 is not
        # contingent on the sender being well-behaved.
        raise IntakeError(
            f"IPC message contains fields not in the allow-list: "
            f"{sorted(extra)}"
        )

    dim_id = message.get("dim_id")
    if not isinstance(dim_id, str) or not dim_id:
        raise IntakeError("IPC message missing dim_id")

    entry = {k: v for k, v in message.items() if k != "dim_id"}
    eval_outputs = {dim_id: entry}

    # Δ1 — verbatim wrap. NOT a re-implementation, NOT a fork.
    enforce_hosted_eval_only(eval_outputs)
    enforce_input_safety(eval_outputs)

    return dim_id, entry


# ---------------------------------------------------------------------------
# BEAAA-2182 Track A — Scorer→API IPC envelope return channel.
#
# The scoring projection runs INSIDE the Scorer subprocess (inside the
# deny-all ``ScopedEgressAllowlist([])`` scope per Hard Invariant #5).
# The §2.2 envelope it produces is shipped back to the API-edge parent
# over a separate return-queue, validated against the closed
# :data:`ALLOWED_SCORE_ENVELOPE_FIELDS` allow-list on both serialize and
# receive sides.
# ---------------------------------------------------------------------------


def serialize_score_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Scorer-side. Validate and return a sendable envelope dict.

    Raises :class:`IPCSchemaError` BEFORE the dict goes on the wire if it
    carries any field outside :data:`ALLOWED_SCORE_ENVELOPE_FIELDS`, any
    value whose type is not a JSON primitive, any ``bytes`` /
    ``bytearray`` / opaque object, or any instance of
    :class:`~harness.credentials.CredentialVault` /
    :class:`~harness.credentials.CredentialRef` (which should never
    appear in a scoring envelope but the check is defense in depth).

    Mirrors :func:`serialize_measurement_row` exactly except for the
    allow-list set and the byte cap. The two serializers share the
    :func:`_is_pure_json_value` helper so behavior is consistent.
    """
    if not isinstance(envelope, dict):
        raise IPCSchemaError(
            f"envelope must be a dict, got {type(envelope).__name__}"
        )

    extra = set(envelope) - ALLOWED_SCORE_ENVELOPE_FIELDS
    if extra:
        raise IPCSchemaError(
            f"envelope contains fields not in the IPC allow-list: "
            f"{sorted(extra)}. Allowed: "
            f"{sorted(ALLOWED_SCORE_ENVELOPE_FIELDS)}. Anything not in "
            "this list is rejected before transit (Hard Invariant #4)."
        )

    for k, v in envelope.items():
        if isinstance(v, _FORBIDDEN_VALUE_TYPES):
            raise IPCSchemaError(
                f"{k!r}: {type(v).__name__} is forbidden in IPC payloads "
                "(opaque bytes could smuggle a credential)."
            )
        cls_name = type(v).__name__
        if cls_name in ("CredentialVault", "CredentialRef"):
            raise IPCSchemaError(
                f"{k!r}: {cls_name} instances must never cross the IPC "
                "boundary (Hard Invariant #4)."
            )
        if not _is_pure_json_value(v):
            raise IPCSchemaError(
                f"{k!r}: value of type {type(v).__name__} is not a JSON "
                "primitive; IPC payloads must be JSON-pure."
            )

    import json
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_IPC_ENVELOPE_BYTES:
        raise IPCSchemaError(
            f"serialized envelope exceeds {MAX_IPC_ENVELOPE_BYTES} bytes "
            f"({len(encoded)} chars) — refusing to enqueue."
        )

    return envelope


def validate_inbound_envelope(message: Any) -> dict[str, Any]:
    """Parent-side. Validate an IPC envelope message and return the
    envelope dict.

    Defense in depth: even though the Scorer-side serializer rejects
    extras, the parent re-checks. Hard Invariant #4 is not contingent on
    the Scorer being well-behaved.

    Does NOT call the intake-guard validators
    (``enforce_hosted_eval_only`` / ``enforce_input_safety``) — those are
    row-shape validators inappropriate for an envelope dict. The closed
    allow-list + JSON-primitive type check is the binding contract.
    """
    if not isinstance(message, dict):
        raise IPCSchemaError(
            f"envelope IPC message must be a dict, got "
            f"{type(message).__name__}"
        )

    extra = set(message) - ALLOWED_SCORE_ENVELOPE_FIELDS
    if extra:
        raise IPCSchemaError(
            f"envelope IPC message contains fields not in the allow-list: "
            f"{sorted(extra)}"
        )

    for k, v in message.items():
        if isinstance(v, _FORBIDDEN_VALUE_TYPES):
            raise IPCSchemaError(
                f"{k!r}: {type(v).__name__} forbidden in IPC envelope."
            )
        if not _is_pure_json_value(v):
            raise IPCSchemaError(
                f"{k!r}: value of type {type(v).__name__} is not a JSON "
                "primitive."
            )

    return message


# ---------------------------------------------------------------------------
# Process model — Prober and Scorer spawn helpers.
# ---------------------------------------------------------------------------


def _fork_context() -> mp_context.BaseContext:
    """Return the fork-based multiprocessing context.

    Forced to ``"fork"`` so the API-edge ``CredentialVault`` is inherited
    into the Prober child via copy-on-write memory rather than being
    pickled (vault.__reduce__ refuses to carry secrets across pickle).
    Linux-only by construction; the live-probe service deployment
    target is Linux.
    """
    if sys.platform == "win32":  # pragma: no cover — service is Linux-only
        raise RuntimeError(
            "live_probe_workers requires fork-based multiprocessing; "
            "Windows is not a supported deployment target for the "
            "Prober/Scorer split."
        )
    return mp.get_context("fork")


class WorkerChannel:
    """Container for the Prober↔Scorer IPC queue + lifecycle bits.

    Wraps two ``multiprocessing.Queue`` instances:

    * The row queue (Prober → Scorer) carries one validated
      measurement row per put. Send via :meth:`send_row`; receive via
      :meth:`recv_row`. Sends are routed through
      :func:`serialize_measurement_row` and receives through
      :func:`validate_inbound`.
    * The score-envelope return queue (Scorer → API-edge parent)
      carries exactly one envelope tuple per scan (BEAAA-2182 Track A).
      Send via :meth:`send_score_envelope`; receive via
      :meth:`recv_score_envelope`. Sends are routed through
      :func:`serialize_score_envelope` (on the ``"ok"`` branch) and
      receives through :func:`validate_inbound_envelope`.

    Callers do not touch either raw queue.
    """

    def __init__(self, *, max_queue: int = 256) -> None:
        ctx = _fork_context()
        self._ctx = ctx
        self._queue: mp_queues.Queue = ctx.Queue(maxsize=max_queue)
        # BEAAA-2182 Track A — return queue. maxsize=1 because exactly
        # one envelope is shipped per scan (success OR error tuple).
        self._envelope_queue: mp_queues.Queue = ctx.Queue(maxsize=1)

    @property
    def context(self) -> mp_context.BaseContext:
        return self._ctx

    @property
    def queue(self) -> mp_queues.Queue:
        return self._queue

    @property
    def envelope_queue(self) -> mp_queues.Queue:
        return self._envelope_queue

    def send_row(self, dim_id: str, row: dict[str, Any], *,
                 timeout: float | None = None) -> None:
        """Prober side. Validate + enqueue one row.

        Raises :class:`IPCSchemaError` BEFORE enqueueing if the row
        carries a forbidden field / type. The bad row never reaches the
        queue, so the Scorer never sees it.
        """
        payload = serialize_measurement_row(dim_id, row)
        self._queue.put(payload, timeout=timeout)

    def send_end_of_scan(self, *, timeout: float | None = None) -> None:
        self._queue.put(END_OF_SCAN, timeout=timeout)

    def recv_row(self, *, timeout: float | None = None) -> Any:
        """Scorer side. Pop one message from the queue.

        Returns the message verbatim — the caller is responsible for
        invoking :func:`validate_inbound` (or checking for
        :data:`END_OF_SCAN`). Splitting recv and validate keeps the
        validator independently testable on synthesized messages.
        """
        return self._queue.get(timeout=timeout)

    def send_score_envelope(
        self,
        outcome: tuple,
        *,
        timeout: float | None = None,
    ) -> None:
        """Scorer side. Validate + enqueue the score-envelope tuple.

        ``outcome`` is exactly one of:

        * ``("ok", envelope_dict)`` — the success path. The envelope
          dict is validated through :func:`serialize_score_envelope`
          BEFORE the tuple goes on the wire. A failed validation raises
          :class:`IPCSchemaError` and the bad dict never reaches the
          parent.
        * ``("err", type_name, message)`` — the error path. Mirrors the
          Prober's outcome tuple shape exactly (Q-A2 ACCEPT on
          BEAAA-2182 wake comment 9840343c). The parent side raises
          :class:`OrchestratorError` from this tuple.

        Any other shape raises :class:`IPCSchemaError` BEFORE enqueueing.
        """
        if not isinstance(outcome, tuple) or len(outcome) < 2:
            raise IPCSchemaError(
                f"score envelope outcome must be ('ok', dict) or "
                f"('err', type, msg); got {outcome!r}"
            )
        tag = outcome[0]
        if tag == "ok":
            if len(outcome) != 2 or not isinstance(outcome[1], dict):
                raise IPCSchemaError(
                    f"('ok', envelope) requires envelope as dict; got "
                    f"{outcome!r}"
                )
            serialize_score_envelope(outcome[1])
            self._envelope_queue.put(outcome, timeout=timeout)
            return
        if tag == "err":
            if len(outcome) != 3 or not isinstance(outcome[1], str) or not isinstance(outcome[2], str):
                raise IPCSchemaError(
                    f"('err', type, msg) requires two strs; got "
                    f"{outcome!r}"
                )
            self._envelope_queue.put(outcome, timeout=timeout)
            return
        raise IPCSchemaError(
            f"unknown score envelope tag {tag!r}; expected 'ok' | 'err'"
        )

    def recv_score_envelope(
        self,
        *,
        timeout: float | None = None,
    ) -> tuple:
        """Parent side. Pop the score-envelope tuple from the return queue.

        Returns the tuple verbatim — caller is responsible for branching
        on ``outcome[0] in {'ok', 'err'}`` and invoking
        :func:`validate_inbound_envelope` on the dict (defense in depth).
        """
        return self._envelope_queue.get(timeout=timeout)


def run_prober(
    *,
    channel: WorkerChannel,
    agent_endpoint_host: str,
    probe_fn: Callable[[ScopedEgressAllowlist], Iterable[tuple[str, dict[str, Any]]]],
) -> None:
    """Prober subprocess entrypoint.

    Opens :class:`ScopedEgressAllowlist` around the customer's
    ``agent_endpoint_host`` for the lifetime of the call, drives
    ``probe_fn`` (which yields ``(dim_id, row)`` pairs), and pushes each
    row onto the IPC queue via :meth:`WorkerChannel.send_row`. After
    ``probe_fn`` returns, an :data:`END_OF_SCAN` sentinel is pushed so
    the Scorer's drain loop terminates.

    ``probe_fn`` is the integration point with the eval-framework
    wrappers (``scanners/promptfoo_runner.py``,
    ``scanners/inspect_runner.py``). It receives the active egress
    scope so it can construct an httpx client bound to the allow-list.
    The orchestrator (ADR-0009 §5 #2) is responsible for resolving the
    credential via the inherited vault before invoking ``probe_fn``;
    the credential never appears in any argument to this function.

    Per-row IPC schema failures are surfaced to the Prober's stdout /
    log channel; they do NOT abort the whole probe run, so a single
    malformed row from a misbehaving probe wrapper does not crater the
    scan. The Scorer-side validator is the authoritative check.
    """
    with ScopedEgressAllowlist(agent_endpoint_host) as scope:
        try:
            for dim_id, row in probe_fn(scope):
                try:
                    channel.send_row(dim_id, row)
                except IPCSchemaError as exc:
                    # The malformed row is dropped before transit; the
                    # Scorer never observes it. Log to stderr so the
                    # scan-lifecycle audit captures the drop.
                    print(
                        f"[prober] IPCSchemaError on dim_id={dim_id!r}: {exc}",
                        file=sys.stderr,
                    )
        finally:
            channel.send_end_of_scan()


def _collapse_replicates_to_median_rows(
    accumulated_replicates: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Collapse per-dim replicate entry lists to one median-representative
    row per dim (BEAAA-14422 / code-review F3).

    The Prober sends EVERY replicate row over IPC, so the Scorer sees K
    entries per dim. Scoring the *last* one (last-write-wins) diverges from
    the orchestrator's ``ScanResult.dim_results[dim].raw`` — which is
    ``_median_of`` of the K replicate raws — the moment the agent is
    nondeterministic (median != last). This collapses each dim to the raw
    the orchestrator reports, using the SHARED
    :func:`scanners.replicate_median._median_of` (the same function the
    orchestrator imports — not reimplemented) so the two authoritative
    surfaces are byte-identical BY CONSTRUCTION.

    Because ``_median_of`` uses the lower-of-two-middles convention, the
    median is ALWAYS a real replicate value (both K=5 and K=10). We select
    the replicate ENTRY whose ``raw`` equals that median — deterministic
    tie-break: the lowest replicate index, i.e. the first such entry in
    receive order — as the representative, so ``evidence_uri`` /
    ``evidence_sha256`` keep pointing at a real transcript that actually
    produced the scored raw. The median value itself is order-independent,
    so the scored raw is stable regardless of IPC receive order.
    """
    collapsed: dict[str, dict[str, Any]] = {}
    for dim_id, entries in accumulated_replicates.items():
        if not entries:
            continue
        raws = [float(entry["raw"]) for entry in entries]
        median = _median_of(raws)
        # lower-of-two-middles => median is a real replicate value; pick the
        # first entry that produced it (lowest replicate index tie-break).
        representative = next(
            (entry for entry, raw in zip(entries, raws) if raw == median),
            entries[0],
        )
        collapsed[dim_id] = representative
    return collapsed


def run_scorer(
    *,
    channel: WorkerChannel,
    on_validated_row: Callable[[str, dict[str, Any]], None],
    on_validation_error: Callable[[Any, Exception], None] | None = None,
    score_runtime: Any = None,
    score_inputs: dict[str, Any] | None = None,
    row_recv_timeout_seconds: float | None = None,
) -> None:
    """Scorer subprocess entrypoint.

    Opens :class:`ScopedEgressAllowlist` with ``[]`` (deny-all) for the
    lifetime of the call, drains the IPC queue, runs the verbatim
    intake-guard validators on every message, and forwards each
    validated ``(dim_id, entry)`` to ``on_validated_row``. Stops on
    :data:`END_OF_SCAN`.

    Validation errors are passed to ``on_validation_error`` (or
    re-raised if no handler is provided). The bad message is NOT
    forwarded to ``on_validated_row`` — Hard Invariant #3 means the
    scoring engine never sees an unvalidated row.

    BEAAA-2182 Track A — when ``score_runtime`` is provided (a
    :class:`scanners.score_runtime.ScoreRuntime` instance fork-inherited
    from the API-edge parent), the Scorer ALSO accumulates the
    validated rows into a ``dim_id → entry`` dict, then on END_OF_SCAN
    invokes ``score_runtime.compute_envelope(accumulated_rows=...,
    **score_inputs)`` and ships the resulting §2.2 envelope back to the
    parent over :meth:`WorkerChannel.send_score_envelope`. Errors from
    the projection are surfaced as ``("err", type_name, message)``
    tuples mirroring :func:`api.scan_runtime_factory._prober_target`
    (Q-A2 ACCEPT on BEAAA-2182 wake comment ``9840343c``).

    ``row_recv_timeout_seconds`` bounds the Scorer's wait for each row
    or END_OF_SCAN sentinel. ``None`` preserves the legacy unbounded
    wait for direct unit-call compatibility; production spawn wiring
    passes the BEAAA-7837 default.

    When ``score_runtime`` is ``None`` (legacy / flag-off path), the
    Scorer behaves as it did pre-Track-A: drain + validate + forward,
    no return-channel emit. Backward-compatible default.
    """
    # AG-A5 paranoia layer (Q-A3 ACCEPT on BEAAA-2182 wake comment
    # 9840343c). Runs BEFORE the deny-all egress scope opens so the
    # AssertionError doesn't get masked. Only fires under ARE_TEST_MODE=1
    # to avoid prod startup overhead.
    if os.environ.get("ARE_TEST_MODE") == "1":
        assert "api.score_runtime" not in sys.modules, (
            "AG-A5 violation: api.score_runtime imported by parent before "
            "fork (fork-inherits sys.modules); scoring callee transitively "
            "reachable from API edge. The Scorer-side module is "
            "scanners.score_runtime; the api.* path is forbidden by "
            "scripts/check_no_apiedge_score.py."
        )

    # BEAAA-14422 / F3 — accumulate ALL replicate rows per dim (NOT
    # last-write-wins). The list is collapsed to one median-representative
    # row per dim on END_OF_SCAN, so the envelope is scored from the same
    # per-dim median the orchestrator reports on ScanResult.dim_results.
    accumulated_replicates: dict[str, list[dict[str, Any]]] = {}
    with ScopedEgressAllowlist([]):
        while True:
            try:
                message = channel.recv_row(timeout=row_recv_timeout_seconds)
            except queue.Empty as exc:
                timeout_message = (
                    "scorer row queue receive timed out after "
                    f"{row_recv_timeout_seconds}s waiting for row or END_OF_SCAN"
                )
                if score_runtime is not None:
                    try:
                        channel.send_score_envelope(
                            ("err", "TimeoutError", timeout_message)
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    return
                raise TimeoutError(timeout_message) from exc
            if message == END_OF_SCAN:
                break
            try:
                dim_id, entry = validate_inbound(message)
            except (IntakeError, IPCSchemaError) as exc:
                if on_validation_error is None:
                    raise
                on_validation_error(message, exc)
                continue
            on_validated_row(dim_id, entry)
            if score_runtime is not None:
                # BEAAA-14422 / F3 — keep every replicate row per dim so the
                # envelope can be scored from the K-replicate median (the
                # collapse happens on END_OF_SCAN, below). The prior
                # last-write-wins kept only the final replicate, which
                # silently diverged from ScanResult for nondeterministic
                # agents (median != last).
                accumulated_replicates.setdefault(dim_id, []).append(entry)

        # BEAAA-2182 Track A — END_OF_SCAN reached. If a Scorer-side
        # score_runtime was provided, project the §2.2 envelope and ship
        # it back over the return channel. Tuple sentinel mirrors Prober.
        if score_runtime is not None:
            try:
                # BEAAA-14422 / F3 — collapse the per-dim replicate lists to
                # one median-representative row BEFORE scoring, so
                # compute_envelope's one-row-per-dim signature is unchanged
                # and the persisted are_total matches ScanResult by
                # construction (shared _median_of, lower-of-two-middles).
                accumulated = _collapse_replicates_to_median_rows(
                    accumulated_replicates
                )
                # AC#5 permanent guard — the raw about to be scored for each
                # dim MUST equal the shared K-replicate median of that dim's
                # received replicates. This runs on the standard scan path,
                # so any future reintroduction of divergence (e.g. a revert
                # to last-write-wins that bypasses the collapse) fails loudly
                # here — not only in the one replay fixture. Fail-closed: the
                # AssertionError is caught below and shipped as an ("err", …)
                # tuple, so the scan errors rather than persisting a
                # divergent envelope.
                for _dim_id, _row in accumulated.items():
                    _median = _median_of(
                        [float(e["raw"]) for e in accumulated_replicates[_dim_id]]
                    )
                    if float(_row["raw"]) != _median:
                        raise AssertionError(
                            "BEAAA-14422 invariant violated: scored raw "
                            f"{_row['raw']!r} for dim {_dim_id!r} != shared "
                            f"K-replicate median {_median!r}"
                        )
                envelope = score_runtime.compute_envelope(
                    accumulated_rows=accumulated,
                    **(score_inputs or {}),
                )
                channel.send_score_envelope(("ok", envelope))
            except BaseException as exc:  # noqa: BLE001 — surface every failure
                # The parent's recv_score_envelope drain will see this
                # tuple and raise OrchestratorError. The Prober end-path
                # (which already pushed END_OF_SCAN on its error route)
                # means the row queue drain has already returned.
                try:
                    channel.send_score_envelope(
                        ("err", type(exc).__name__, str(exc))
                    )
                except Exception:  # noqa: BLE001
                    # If even the err tuple can't be enqueued (e.g.
                    # queue closed), there's nothing more the Scorer
                    # can do — the parent's drain timeout is the
                    # backstop.
                    pass


def spawn_prober(
    *,
    channel: WorkerChannel,
    agent_endpoint_host: str,
    probe_fn: Callable[[ScopedEgressAllowlist], Iterable[tuple[str, dict[str, Any]]]],
    name: str = "are-prober",
) -> mp_context.SpawnProcess:
    """Fork the Prober subprocess and return the handle.

    Inherits the API-edge :class:`~harness.credentials.CredentialVault`
    via copy-on-write memory. Callers are responsible for invoking
    ``vault.forget_all()`` on the parent side at scan finalize.
    """
    ctx = channel.context
    proc = ctx.Process(
        target=run_prober,
        kwargs={
            "channel": channel,
            "agent_endpoint_host": agent_endpoint_host,
            "probe_fn": probe_fn,
        },
        name=name,
    )
    proc.start()
    return proc


def spawn_scorer(
    *,
    channel: WorkerChannel,
    on_validated_row: Callable[[str, dict[str, Any]], None],
    on_validation_error: Callable[[Any, Exception], None] | None = None,
    score_runtime: Any = None,
    score_inputs: dict[str, Any] | None = None,
    row_recv_timeout_seconds: float | None = DEFAULT_SCORER_ROW_RECV_TIMEOUT_SECONDS,
    name: str = "are-scorer",
) -> mp_context.SpawnProcess:
    """Fork the Scorer subprocess and return the handle.

    The Scorer's egress posture is set inside :func:`run_scorer`
    (``ScopedEgressAllowlist([])``), not at spawn time, so the deny-all
    scope is in effect for the full window in which the Scorer touches
    user-supplied data.

    BEAAA-2182 Track A — when ``score_runtime`` is provided, the kwargs
    flow into :func:`run_scorer` so the Scorer subprocess can project
    the §2.2 envelope on END_OF_SCAN and ship it back to the parent.
    The ``score_runtime`` instance is fork-inherited (loaded once on
    the parent side via :meth:`scanners.score_runtime.ScoreRuntime.from_rubric_path`
    BEFORE spawn — Q-A1 ACCEPT).
    """
    ctx = channel.context
    proc = ctx.Process(
        target=run_scorer,
        kwargs={
            "channel": channel,
            "on_validated_row": on_validated_row,
            "on_validation_error": on_validation_error,
            "score_runtime": score_runtime,
            "score_inputs": score_inputs,
            "row_recv_timeout_seconds": row_recv_timeout_seconds,
        },
        name=name,
    )
    proc.start()
    return proc


__all__ = [
    "ALLOWED_ROW_FIELDS",
    "ALLOWED_SCORE_ENVELOPE_FIELDS",
    "END_OF_SCAN",
    "EgressNotAllowed",
    "IPCSchemaError",
    "IntakeError",
    "DEFAULT_SCORER_ROW_RECV_TIMEOUT_SECONDS",
    "MAX_IPC_ENVELOPE_BYTES",
    "MAX_IPC_ROW_BYTES",
    "ScopedEgressAllowlist",
    "WorkerChannel",
    "run_prober",
    "run_scorer",
    "serialize_measurement_row",
    "serialize_score_envelope",
    "spawn_prober",
    "spawn_scorer",
    "validate_inbound",
    "validate_inbound_envelope",
]
