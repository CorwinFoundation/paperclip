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

    A thin wrapper around ``multiprocessing.Queue`` that exposes only
    :meth:`send_row` (Prober side) and :meth:`recv_row` (Scorer side).
    Callers do not touch the raw queue, so all sends are routed through
    :func:`serialize_measurement_row` and all receives through
    :func:`validate_inbound`.
    """

    def __init__(self, *, max_queue: int = 256) -> None:
        ctx = _fork_context()
        self._ctx = ctx
        self._queue: mp_queues.Queue = ctx.Queue(maxsize=max_queue)

    @property
    def context(self) -> mp_context.BaseContext:
        return self._ctx

    @property
    def queue(self) -> mp_queues.Queue:
        return self._queue

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


def run_scorer(
    *,
    channel: WorkerChannel,
    on_validated_row: Callable[[str, dict[str, Any]], None],
    on_validation_error: Callable[[Any, Exception], None] | None = None,
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

    The scoring engine itself is invoked by the orchestrator (ADR-0009
    §5 #2) once all dims have been accumulated, not per-row inside this
    function. Keeping accumulation in the orchestrator means this
    module stays narrowly about "process model + validated IPC" and
    does not creep into scoring-orchestration responsibilities.
    """
    with ScopedEgressAllowlist([]):
        while True:
            message = channel.recv_row()
            if message == END_OF_SCAN:
                return
            try:
                dim_id, entry = validate_inbound(message)
            except (IntakeError, IPCSchemaError) as exc:
                if on_validation_error is None:
                    raise
                on_validation_error(message, exc)
                continue
            on_validated_row(dim_id, entry)


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
    name: str = "are-scorer",
) -> mp_context.SpawnProcess:
    """Fork the Scorer subprocess and return the handle.

    The Scorer's egress posture is set inside :func:`run_scorer`
    (``ScopedEgressAllowlist([])``), not at spawn time, so the deny-all
    scope is in effect for the full window in which the Scorer touches
    user-supplied data.
    """
    ctx = channel.context
    proc = ctx.Process(
        target=run_scorer,
        kwargs={
            "channel": channel,
            "on_validated_row": on_validated_row,
            "on_validation_error": on_validation_error,
        },
        name=name,
    )
    proc.start()
    return proc


__all__ = [
    "ALLOWED_ROW_FIELDS",
    "END_OF_SCAN",
    "EgressNotAllowed",
    "IPCSchemaError",
    "IntakeError",
    "MAX_IPC_ROW_BYTES",
    "ScopedEgressAllowlist",
    "WorkerChannel",
    "run_prober",
    "run_scorer",
    "serialize_measurement_row",
    "spawn_prober",
    "spawn_scorer",
    "validate_inbound",
]
