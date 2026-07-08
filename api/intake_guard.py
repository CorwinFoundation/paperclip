"""BEAAA-22 §3 hosted-eval-only intake guard (Task D).

v0.1 accepts evidence only when each dim entry carries provenance proving it was
produced by the ARE hosted eval runner. Self-reported / customer-uploaded
results are rejected — that policy is enforced at the API edge so the scoring
engine never sees unverified raw scores.

v0.1.4 additive (BEAAA-163 Option C/B+C tamper-resistance pass): scanner
inputs are themselves an adversarial surface — a scanned agent's output can
flow into eval_outputs and attempt to inject downstream consumers (forensic
bundle reader, report-card renderer, log pipeline). enforce_input_safety()
is defense-in-depth on top of provenance: it bounds string sizes, rejects
control characters, enforces https:// on evidence_uri, validates
evidence_sha256 shape, and rejects non-numeric raw smuggling.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

HOSTED_RUNNER = "beaai-hosted-eval"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
# Two non-overlapping shapes: legacy `P<n>.<m>` (rubric v1.0.0) and fully-qualified
# `PILLAR.FAMILY.SUB` (rubric v1.0.1, strict 3-segment all-caps). ADR-0027 (BEAAA-2198).
# The legacy shape is locked to P1–P4: rubric has exactly 4 pillars and pillar-5+ is
# explicitly out of scope. BEAAA-5270 / ADR-0027 addendum.
_DIM_ID_RE = re.compile(
    r"^(?:P[1-4]\.[1-9][0-9]?|[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*)$"
)
_EVIDENCE_URI_MAX = 2048
_DIM_ID_MAX = 64
_MAX_DIMS = 64

# Control chars: anything below 0x20 except tab/newline/cr; plus DEL (0x7F).
# Newline/tab in evidence_uri or evidence_sha256 is still rejected (URIs
# don't legitimately contain them); the broader check is for any string
# field that could be log-injection or terminal-escape vector.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")


class IntakeError(ValueError):
    pass


# BEAAA-10920 (SEC-6) — server-issued provenance integrity.
#
# The dynamic test pack (BEAAA-10913) confirmed the live edge accepts a
# client-set ``runner=beaai-hosted-eval`` plus a self-minted UUID
# ``eval_run_id`` as proof the evidence came from the hosted eval runner. A
# well-formed string and a well-formed UUID are NOT proof of origin — a
# customer can forge both. Per required scope #4, provenance is now bound to a
# *server-issued integrity mechanism*: the hosted runner stamps each dim entry
# with ``provenance_sig`` = HMAC-SHA256(server_key,
# "<dim_id>\n<runner>\n<eval_run_id>"). The API edge verifies the tag with the
# same server-held key; a forged/absent tag is rejected before scoring.
#
# Posture mirrors ``ARE_BUNDLE_SIGNING_KEY`` (api/bundle.py) and the
# live-egress fail-closed gate (BEAAA-10458): the *live edge* is wired with a
# real key via ``ARE_EVAL_PROVENANCE_KEY`` and therefore fail-closes on forged
# provenance; offline replay / unit tests / the golden N=5 harness run with NO
# key and are byte-for-byte unchanged (verifier is inert → legacy string+UUID
# check only), so the calibration delta is 0 by construction.
_PROVENANCE_KEY_ENV = "ARE_EVAL_PROVENANCE_KEY"
_PROVENANCE_SIG_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def provenance_preimage(dim_id: str, runner: str, eval_run_id: str) -> bytes:
    """Canonical bytes the provenance HMAC is computed over.

    Newline-delimited so no field can smuggle a delimiter and re-partition the
    preimage. Kept in one place so the hosted runner (signer) and the API edge
    (verifier) cannot drift.
    """
    return f"{dim_id}\n{runner}\n{eval_run_id}".encode("utf-8")


def sign_eval_provenance(key: bytes, dim_id: str, runner: str, eval_run_id: str) -> str:
    """Return the hex HMAC-SHA256 provenance tag for one dim entry.

    Server-side only — the hosted eval runner calls this with the shared
    ``ARE_EVAL_PROVENANCE_KEY`` when it emits ``eval_outputs``. Exposed here so
    signer and verifier share the exact construction and so the red-line tests
    can mint a *valid* tag to prove the accept path still works.
    """
    return hmac.new(
        key, provenance_preimage(dim_id, runner, eval_run_id), hashlib.sha256
    ).hexdigest()


class EvalProvenanceVerifier:
    """Verifies server-issued provenance tags on hosted-eval evidence.

    Constructed with the server-held HMAC key. ``verify`` raises
    :class:`IntakeError` when the entry's ``provenance_sig`` is missing,
    malformed, or does not match the tag the server would have issued for that
    ``(dim_id, runner, eval_run_id)``. Comparison is constant-time.
    """

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("EvalProvenanceVerifier requires a non-empty key")
        self._key = key

    @classmethod
    def from_env(cls) -> "EvalProvenanceVerifier | None":
        """Build a verifier from ``ARE_EVAL_PROVENANCE_KEY``, or ``None``.

        Returns ``None`` when the env var is unset/empty — the offline default
        posture that keeps replay/tests/golden byte-identical. The live edge
        deployment sets the key, which flips the intake guard to fail-closed on
        forged provenance.
        """
        raw = os.environ.get(_PROVENANCE_KEY_ENV)
        if not raw:
            return None
        return cls(raw.encode("utf-8"))

    def verify(self, dim_id: str, runner: str, eval_run_id: str, sig: Any) -> None:
        if not isinstance(sig, str) or not _PROVENANCE_SIG_RE.match(sig):
            raise IntakeError(
                f"{dim_id}: provenance_sig missing or malformed; hosted-eval "
                f"evidence must carry a server-issued HMAC provenance tag"
            )
        expected = sign_eval_provenance(self._key, dim_id, runner, eval_run_id)
        if not hmac.compare_digest(sig, expected):
            raise IntakeError(
                f"{dim_id}: provenance_sig does not match a server-issued tag; "
                f"client-asserted runner/eval_run_id is not trusted"
            )


class ProvenanceSigner:
    """Producer-side counterpart of :class:`EvalProvenanceVerifier` (BEAAA-10945).

    The hosted eval runner constructs this from the shared
    ``ARE_EVAL_PROVENANCE_KEY`` and calls :meth:`stamp_eval_outputs` on the
    ``eval_outputs`` mapping it is about to submit to ``POST /score``. Each dim
    entry gains a ``provenance_sig`` = HMAC-SHA256(key,
    "<dim_id>\\n<runner>\\n<eval_run_id>") — the *exact* construction the API
    edge verifies via :func:`sign_eval_provenance`. Signer and verifier share
    that one function so the live edge and the runner cannot drift (the lockstep
    requirement flagged on BEAAA-10920: without a runner signing in lockstep
    with the live key, ``/score`` rejects all hosted evidence).

    Fail-closed by construction: an entry that does not already carry a
    ``runner``/``eval_run_id`` cannot be signed (raises) — the runner must have
    stamped hosted-eval provenance first (see
    ``scanners.live_probe_orchestrator``). Only ``HOSTED_RUNNER`` entries are
    signed; a non-hosted ``runner`` would be rejected by
    :func:`enforce_hosted_eval_only` at the edge regardless, so signing it would
    be meaningless.
    """

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("ProvenanceSigner requires a non-empty key")
        self._key = key

    @classmethod
    def from_env(cls) -> "ProvenanceSigner | None":
        """Build a signer from ``ARE_EVAL_PROVENANCE_KEY``, or ``None``.

        Symmetric with :meth:`EvalProvenanceVerifier.from_env`: ``None`` when the
        env var is unset/empty (offline/dev posture — the runner emits the legacy
        string+UUID provenance and the inert verifier accepts it). The live edge
        sets the key, so the runner signs and the verifier enforces.
        """
        raw = os.environ.get(_PROVENANCE_KEY_ENV)
        if not raw:
            return None
        return cls(raw.encode("utf-8"))

    def sign(self, dim_id: str, runner: str, eval_run_id: str) -> str:
        """Return the provenance tag for one dim entry (thin, shared helper)."""
        return sign_eval_provenance(self._key, dim_id, runner, eval_run_id)

    def stamp_eval_outputs(
        self, eval_outputs: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Stamp ``provenance_sig`` onto every hosted-eval dim entry, in place.

        Returns the same mapping for call-site convenience. Raises
        :class:`IntakeError` if a hosted-eval entry is missing the ``runner`` or
        ``eval_run_id`` the signature binds — that is a runner bug, not a
        client-facing 422, and must surface loudly at emit time rather than ship
        an unsignable payload that the edge will silently reject.
        """
        if not isinstance(eval_outputs, dict) or not eval_outputs:
            raise IntakeError("eval_outputs must be a non-empty mapping to sign")
        for dim_id, entry in eval_outputs.items():
            if not isinstance(entry, dict):
                raise IntakeError(f"{dim_id}: eval_outputs entry must be a mapping")
            runner = entry.get("runner")
            eval_run_id = entry.get("eval_run_id")
            if runner != HOSTED_RUNNER:
                # Non-hosted entries are rejected at the edge before verification;
                # do not sign them (signing would imply hosted origin they lack).
                continue
            if not isinstance(eval_run_id, str) or not _UUID_RE.match(eval_run_id):
                raise IntakeError(
                    f"{dim_id}: cannot sign provenance — eval_run_id missing or "
                    f"not a UUID; the hosted runner must stamp it before signing"
                )
            entry["provenance_sig"] = self.sign(dim_id, runner, eval_run_id)
        return eval_outputs


def enforce_hosted_eval_only(
    eval_outputs: dict[str, dict[str, Any]],
    *,
    provenance_verifier: "EvalProvenanceVerifier | None" = None,
) -> None:
    """Raise IntakeError on any dim entry that lacks hosted-runner provenance.

    BEAAA-10920 (SEC-6): when ``provenance_verifier`` is supplied (live edge,
    ``ARE_EVAL_PROVENANCE_KEY`` wired) each entry must additionally carry a
    valid server-issued ``provenance_sig`` — a client-minted UUID + asserted
    runner string is no longer sufficient. When ``None`` (offline replay /
    tests / golden N=5), the legacy string+UUID check is unchanged so scoring
    outputs are byte-identical.
    """
    if not isinstance(eval_outputs, dict) or not eval_outputs:
        raise IntakeError("eval_outputs must be a non-empty mapping")
    for dim_id, entry in eval_outputs.items():
        if not isinstance(entry, dict):
            raise IntakeError(f"{dim_id}: entry must be an object")
        runner = entry.get("runner")
        if runner != HOSTED_RUNNER:
            raise IntakeError(
                f"{dim_id}: runner={runner!r} not allowed; v0.1 accepts hosted-eval only"
            )
        run_id = entry.get("eval_run_id")
        if not isinstance(run_id, str) or not _UUID_RE.match(run_id):
            raise IntakeError(f"{dim_id}: eval_run_id missing or not a UUID")
        if provenance_verifier is not None:
            provenance_verifier.verify(dim_id, runner, run_id, entry.get("provenance_sig"))


def enforce_input_safety(eval_outputs: dict[str, dict[str, Any]]) -> None:
    """Belt-and-suspenders defense in depth on adversarial scanner inputs.

    Runs alongside enforce_hosted_eval_only at the API edge. Provenance
    establishes WHO produced the eval; this function validates WHAT was
    produced is shape-safe to flow into the scoring engine, forensic
    bundle, and report-card renderer.

    Rejects, with an explicit error per case:
      - eval_outputs map larger than _MAX_DIMS entries (DoS surface)
      - dim_id that doesn't match the canonical Pn.m shape
      - dim_id longer than _DIM_ID_MAX
      - raw that's not int/float (no string-coerced or list-wrapped values)
      - evidence_uri scheme other than https:// (or empty)
      - evidence_uri longer than _EVIDENCE_URI_MAX
      - evidence_sha256 not matching sha256:<64-hex> (or empty)
      - control characters or DEL in any string field
    """
    if not isinstance(eval_outputs, dict):
        raise IntakeError("eval_outputs must be a mapping")
    if len(eval_outputs) > _MAX_DIMS:
        raise IntakeError(
            f"eval_outputs has {len(eval_outputs)} entries; max allowed is {_MAX_DIMS}"
        )

    for dim_id, entry in eval_outputs.items():
        if not isinstance(dim_id, str) or len(dim_id) > _DIM_ID_MAX:
            raise IntakeError(f"dim_id={dim_id!r} too long or not a string")
        if not _DIM_ID_RE.match(dim_id):
            raise IntakeError(
                f"dim_id={dim_id!r} does not match canonical Pn.m shape "
                f"(P1–P4 only) or PILLAR.FAMILY.SUB shape"
            )
        if not isinstance(entry, dict):
            raise IntakeError(f"{dim_id}: entry must be an object")

        raw = entry.get("raw")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise IntakeError(
                f"{dim_id}: raw must be a numeric type, got {type(raw).__name__}"
            )

        for field_name in ("evidence_uri", "evidence_sha256", "runner", "eval_run_id"):
            value = entry.get(field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise IntakeError(
                    f"{dim_id}.{field_name} must be a string, got {type(value).__name__}"
                )
            if _CONTROL_CHARS_RE.search(value):
                raise IntakeError(
                    f"{dim_id}.{field_name} contains control characters"
                )

        evidence_uri = entry.get("evidence_uri", "")
        if evidence_uri:
            if len(evidence_uri) > _EVIDENCE_URI_MAX:
                raise IntakeError(
                    f"{dim_id}.evidence_uri exceeds {_EVIDENCE_URI_MAX} bytes"
                )
            if not evidence_uri.startswith("https://"):
                raise IntakeError(
                    f"{dim_id}.evidence_uri must be https:// (got scheme prefix "
                    f"{evidence_uri.split(':', 1)[0]!r})"
                )

        evidence_sha256 = entry.get("evidence_sha256", "")
        if evidence_sha256 and not _SHA256_RE.match(evidence_sha256):
            raise IntakeError(
                f"{dim_id}.evidence_sha256 must match sha256:<64-hex>"
            )
