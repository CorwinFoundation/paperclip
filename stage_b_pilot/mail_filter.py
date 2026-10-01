"""Fail-closed intake helpers for automatically forwarded merchant mail."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Callable


_DKIM_DOMAIN = re.compile(r"(?:^|;)\s*d=([^;\s]+)", re.IGNORECASE)
_GMAIL_FORWARDING_CODE = re.compile(
    r"(?:confirmation[ \t]+code|verification[ \t]+code)[ \t]*[:\-]?[ \t]*([0-9]{9})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DkimAssessment:
    signing_domain: str | None
    result: str


@dataclass(frozen=True)
class LinkFacts:
    merchant: str
    occurred_at: datetime
    amount: Decimal


def _fields(body: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in body.splitlines():
        name, separator, value = line.partition(":")
        if separator and value.strip():
            values[name.strip().lower()] = value.strip()
    return values


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_required")
    return parsed


def _amount(value: str) -> Decimal:
    normalized = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError("amount_invalid") from exc


def confirmation_facts(body: str) -> LinkFacts:
    values = _fields(body)
    try:
        return LinkFacts(
            merchant=values["merchant"].casefold(),
            occurred_at=_timestamp(values["date"]),
            amount=_amount(values["amount"]),
        )
    except KeyError as exc:
        raise ValueError("confirmation_link_fields_missing") from exc


def check_matches_confirmation(check_body: str, confirmation: LinkFacts) -> bool:
    values = _fields(check_body)
    try:
        return (
            values["merchant"].casefold() == confirmation.merchant
            and _amount(values["amount"]) == confirmation.amount
            and _timestamp(values["window start"])
            <= confirmation.occurred_at
            <= _timestamp(values["window end"])
        )
    except (KeyError, ValueError):
        return False


def gmail_forwarding_code(subject: str, body: str) -> str | None:
    context = f"{subject}\n{body}"
    if "forwarding confirmation" not in context.casefold():
        return None
    match = _GMAIL_FORWARDING_CODE.search(context)
    return match.group(1) if match else None


def original_message(raw_message: bytes) -> bytes:
    """Return an attached RFC 822 message, or the outer message unchanged.

    Only one attached message is accepted. Multiple attachments are ambiguous
    and must not be silently selected by order.
    """
    outer = BytesParser(policy=policy.default).parsebytes(raw_message)
    attached: list[bytes] = []
    for part in outer.walk():
        if part.get_content_type() != "message/rfc822":
            continue
        payload = part.get_payload()
        if isinstance(payload, list):
            attached.extend(item.as_bytes(policy=policy.SMTP) for item in payload)
        elif isinstance(payload, Message):
            attached.append(payload.as_bytes(policy=policy.SMTP))
        elif isinstance(payload, bytes):
            attached.append(payload)
    if len(attached) > 1:
        raise ValueError("multiple_eml_attachments")
    return attached[0] if attached else raw_message


def assess_dkim(raw_message: bytes, verifier: Callable[[bytes], bool]) -> DkimAssessment:
    """Verify the original message and retain the asserted signing domain."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    headers = message.get_all("DKIM-Signature", [])
    if not headers:
        return DkimAssessment(None, "missing")
    domains = {
        match.group(1).lower().rstrip(".")
        for header in headers
        if (match := _DKIM_DOMAIN.search(str(header)))
    }
    if len(domains) != 1:
        return DkimAssessment(None, "failed")
    try:
        verified = bool(verifier(raw_message))
    except Exception:
        verified = False
    return DkimAssessment(next(iter(domains)), "verified" if verified else "failed")
