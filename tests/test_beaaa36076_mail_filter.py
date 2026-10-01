from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

from stage_b_pilot.mail_filter import assess_dkim, gmail_forwarding_code, original_message


def merchant_message(*, dkim_domain: str | None = "merchant.example") -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "Merchant <confirm@merchant.example>"
    message["To"] = "user@example.com"
    message["Subject"] = "Booking confirmed"
    if dkim_domain:
        message["DKIM-Signature"] = f"v=1; a=rsa-sha256; d={dkim_domain}; s=mail; b=abc"
    message.set_content("Merchant: Example\nAmount: USD 25.00")
    return message.as_bytes()


def forwarded_with_attachment(inner: bytes, *, twice: bool = False) -> bytes:
    outer = EmailMessage(policy=policy.SMTP)
    outer["From"] = "user@example.com"
    outer["To"] = "check@inbound.backbond.ai"
    outer["Subject"] = "Fwd: Booking confirmed"
    outer.set_content("Automatically forwarded confirmation")
    outer.add_attachment(BytesParser(policy=policy.SMTP).parsebytes(inner), filename="confirmation.eml")
    if twice:
        outer.add_attachment(BytesParser(policy=policy.SMTP).parsebytes(inner), filename="second.eml")
    return outer.as_bytes()


def test_attached_eml_is_the_original_verified_message() -> None:
    inner = merchant_message()
    extracted = original_message(forwarded_with_attachment(inner))
    result = assess_dkim(extracted, lambda raw: raw == extracted)
    assert result.signing_domain == "merchant.example"
    assert result.result == "verified"


def test_direct_forward_without_attachment_keeps_outer_raw_message() -> None:
    raw = merchant_message()
    assert original_message(raw) == raw


def test_missing_and_failed_dkim_never_become_verified() -> None:
    assert assess_dkim(merchant_message(dkim_domain=None), lambda _: True).result == "missing"
    failed = assess_dkim(merchant_message(), lambda _: False)
    assert failed == type(failed)("merchant.example", "failed")


def test_verifier_error_and_ambiguous_attachment_fail_closed() -> None:
    def broken(_: bytes) -> bool:
        raise RuntimeError("dns unavailable")

    assert assess_dkim(merchant_message(), broken).result == "failed"
    with pytest.raises(ValueError, match="multiple_eml_attachments"):
        original_message(forwarded_with_attachment(merchant_message(), twice=True))


def test_gmail_forwarding_code_is_extracted_only_from_confirmation_context() -> None:
    body = "Gmail Forwarding Confirmation\nConfirmation code: 123456789"
    assert gmail_forwarding_code("Forwarding Confirmation", body) == "123456789"
    assert gmail_forwarding_code("Receipt", "Confirmation code: 123456789") is None
    assert gmail_forwarding_code("Forwarding Confirmation", "Code: 123456789") is None
