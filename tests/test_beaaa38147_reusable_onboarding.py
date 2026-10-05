from datetime import datetime, timezone

import pytest

from stage_b_pilot.onboarding import OnboardingStore
from stage_b_pilot.receipt_page import render_onboarding


def test_two_users_share_entry_without_sharing_identity(tmp_path):
    store = OnboardingStore(str(tmp_path / "onboarding.sqlite3"))
    first = store.issue("first@example.com", "first@instinct.example")
    second = store.issue("second@example.com", "second@instinct.example")

    assert first != second
    assert store.consume(first) == ("first@example.com", "first@instinct.example")
    assert store.consume(second) == ("second@example.com", "second@instinct.example")
    with pytest.raises(PermissionError, match="invalid_or_expired"):
        store.consume(first)
    store.close()


def test_shared_entry_requires_consent_and_states_demo_boundaries():
    page = render_onboarding()
    assert 'action="/stage-b-demo/onboard/request"' in page
    assert 'name="consent"' in page and 'name="boundaries"' in page
    assert "fictional Instinct history" in page
    assert "cannot buy, book, contact anyone, access a mailbox, move money, or activate protection" in page
    assert "DEMO PRIVATE" in page


def test_email_link_expiry_is_enforced(tmp_path):
    current = [datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)]
    store = OnboardingStore(str(tmp_path / "onboarding.sqlite3"), now=lambda: current[0])
    token = store.issue("first@example.com", "first@instinct.example")
    current[0] = datetime(2026, 10, 5, 12, 21, tzinfo=timezone.utc)
    with pytest.raises(PermissionError, match="invalid_or_expired"):
        store.consume(token)
    store.close()
