"""Secret-family floor non-relaxation per Schema §Redaction policy classes.

The floor pass MUST run before the declared-class pass and MUST scrub all
SECRET_PATTERNS irrespective of declared class. A `clear`-declared field with
a secret-family match is scrubbed to `[REDACTED:secret_family]` before being
emitted.
"""

from __future__ import annotations

import pytest

from post_bind_ingest.normalizer.redaction import (
    SECRET_FAMILY_REPLACEMENT,
    apply_field_redaction,
    floor_scrub_secrets,
)


def test_floor_scrubs_aws_access_key() -> None:
    out, altered = floor_scrub_secrets("token=AKIAIOSFODNN7EXAMPLE end")
    assert altered is True
    assert "AKIA" not in out
    assert SECRET_FAMILY_REPLACEMENT in out


def test_floor_scrubs_bearer_token() -> None:
    out, altered = floor_scrub_secrets("Authorization: Bearer abcd1234-efgh5678ijkl9012mnop")
    assert altered is True
    assert "Bearer abcd" not in out


def test_floor_scrubs_jwt() -> None:
    out, altered = floor_scrub_secrets(
        "session=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwiZXhwIjoyNTQzfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c done"
    )
    assert altered is True
    assert "eyJhbGciOi" not in out


def test_floor_passthrough_on_clean_text() -> None:
    out, altered = floor_scrub_secrets("just a benign string")
    assert altered is False
    assert out == "just a benign string"


def test_clear_class_still_gets_floor_scrub() -> None:
    """Floor non-relaxation: clear-declared fields still pass through the floor."""
    out, applied = apply_field_redaction(
        "token=AKIAIOSFODNN7EXAMPLE end",
        "clear",
        "tool_scope",
    )
    assert "AKIA" not in out
    assert applied == ["tool_scope"]


def test_drop_class_returns_sentinel() -> None:
    from post_bind_ingest.normalizer.redaction import _DROPPED

    out, applied = apply_field_redaction("anything", "drop", "secret_field")
    assert out is _DROPPED
    assert applied == ["secret_field"]


def test_hash_class_yields_sha256_prefix() -> None:
    out, applied = apply_field_redaction({"a": 1}, "hash", "args")
    assert isinstance(out, str) and out.startswith("sha256:") and len(out) == 7 + 64
    assert applied == ["args"]


def test_redact_on_receipt_replaces_pii_only_when_present() -> None:
    out, applied = apply_field_redaction(
        "contact me at alice@example.com today",
        "redact-on-receipt",
        "completion_preview",
    )
    assert "alice@example.com" not in out
    assert "[REDACTED:email]" in out
    assert applied == ["completion_preview"]

    out_clean, applied_clean = apply_field_redaction(
        "nothing sensitive here",
        "redact-on-receipt",
        "completion_preview",
    )
    assert out_clean == "nothing sensitive here"
    assert applied_clean == []


def test_unknown_class_rejected() -> None:
    with pytest.raises(ValueError, match="unknown redaction class"):
        apply_field_redaction("x", "passthrough_lol", "field")
