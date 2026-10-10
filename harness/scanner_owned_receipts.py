"""Authenticated, scanner-owned receipts for offline side-effect probes.

The target never receives the signing key.  It may request a tool call, but only
the scanner proxy can execute the controlled tool and append an observation.
Arbitrary customer HTTP adapters intentionally have no path into this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import time
from typing import Any, Mapping


SCHEMA = "scanner-owned-side-effect-receipt/v1"


class ReceiptRejected(ValueError):
    """The supplied evidence is not current scanner-owned evidence."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass(frozen=True)
class ReceiptIdentity:
    job_id: str
    scenario_id: str
    attempt_id: str
    principal_id: str
    tool_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "scenario_id": self.scenario_id,
            "attempt_id": self.attempt_id,
            "principal_id": self.principal_id,
            "tool_id": self.tool_id,
        }


class ReceiptAuthority:
    """Run-local receipt signer/verifier owned by the scanner runtime."""

    def __init__(self, key: bytes, *, clock=time.time, max_age_seconds: float = 300.0):
        if len(key) < 32:
            raise ValueError("receipt key must contain at least 32 bytes")
        self.__key = bytes(key)
        self._clock = clock
        self._max_age_seconds = max_age_seconds

    def issue(
        self, *, identity: ReceiptIdentity, ledger: list[dict[str, Any]],
        before_state: Any, after_state: Any, outcome: str,
    ) -> dict[str, Any]:
        body = {
            "schema": SCHEMA,
            "issuer": "scanner-runtime",
            "issued_at": float(self._clock()),
            "identity": identity.as_dict(),
            "ledger": ledger,
            "before_state": before_state,
            "after_state": after_state,
            "outcome": outcome,
            # Metering is unknown unless the scanner actually observes it.
            "spend": {"estimated_cost_usd": None, "request_count": None},
        }
        return {"body": body, "mac": hmac.new(self.__key, _canonical(body), sha256).hexdigest()}

    def verify(
        self, envelope: Mapping[str, Any], *, expected: ReceiptIdentity,
    ) -> dict[str, Any]:
        body = envelope.get("body")
        mac = envelope.get("mac")
        if not isinstance(body, Mapping) or not isinstance(mac, str):
            raise ReceiptRejected("malformed_receipt")
        wanted = hmac.new(self.__key, _canonical(body), sha256).hexdigest()
        if not hmac.compare_digest(mac, wanted):
            raise ReceiptRejected("forged_or_target_authored_receipt")
        if body.get("schema") != SCHEMA or body.get("issuer") != "scanner-runtime":
            raise ReceiptRejected("unsupported_receipt_issuer")
        if body.get("identity") != expected.as_dict():
            raise ReceiptRejected("identity_or_attempt_mismatch")
        try:
            age = float(self._clock()) - float(body["issued_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReceiptRejected("invalid_receipt_time") from exc
        if age < 0 or age > self._max_age_seconds:
            raise ReceiptRejected("stale_receipt")
        if not isinstance(body.get("ledger"), list):
            raise ReceiptRejected("invalid_ledger")
        if "before_state" not in body or "after_state" not in body:
            raise ReceiptRejected("missing_state_observation")
        return dict(body)


def evidence_from_receipt(
    authority: ReceiptAuthority, envelope: Mapping[str, Any], identity: ReceiptIdentity,
) -> dict[str, Any] | None:
    """Fail closed: invalid, stale, or mismatched evidence is ungradeable."""
    try:
        return authority.verify(envelope, expected=identity)
    except ReceiptRejected:
        return None

