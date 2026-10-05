from datetime import datetime, timezone

import pytest

from stage_b_pilot.onboarding import OnboardingStore
from stage_b_pilot.receipt_page import enrollment_instruction, render_onboarding


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


def test_shared_entry_is_one_copy_action_without_account_ui():
    instruction = enrollment_instruction("secret-session", "https://scanner.backbond.ai/stage-b-demo")
    page = render_onboarding(instruction)
    assert "Copy instruction" in page
    assert "secret-session" in page
    assert "I consent" in page and "fictional-only" in page
    assert "Stop on expiry, revocation" in page
    for forbidden in ('<form', 'type="email"', "sign-in", "Onboarding history"):
        assert forbidden not in page
    assert "DEMO PRIVATE" in page


def test_email_link_expiry_is_enforced(tmp_path):
    current = [datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)]
    store = OnboardingStore(str(tmp_path / "onboarding.sqlite3"), now=lambda: current[0])
    token = store.issue("first@example.com", "first@instinct.example")
    current[0] = datetime(2026, 10, 5, 12, 21, tzinfo=timezone.utc)
    with pytest.raises(PermissionError, match="invalid_or_expired"):
        store.consume(token)
    store.close()


def test_generic_sessions_are_isolated_one_use_and_independently_revocable(tmp_path):
    store = OnboardingStore(str(tmp_path / "onboarding.sqlite3"))
    first, first_account = store.issue_session()
    second, second_account = store.issue_session()
    assert first != second and first_account != second_account
    assert store.claim_session(first, "first@instinct.example") == first_account
    store.activate_session(first, "first-upstream", "first-grant")
    assert store.stop_credentials(first) == (
        first_account, "first@instinct.example", "first-upstream", "first-grant"
    )
    with pytest.raises(PermissionError, match="invalid_or_expired"):
        store.claim_session(first, "other@instinct.example")
    assert store.revoke_session(second)
    with pytest.raises(PermissionError, match="invalid_or_expired"):
        store.claim_session(second, "second@instinct.example")
    store.close()


def test_session_stop_credentials_do_not_cross_accounts(tmp_path):
    store = OnboardingStore(str(tmp_path / "onboarding.sqlite3"))
    first, first_account = store.issue_session()
    second, second_account = store.issue_session()
    store.claim_session(first, "first@instinct.example")
    store.claim_session(second, "second@instinct.example")
    store.activate_session(first, "first-upstream", "first-grant")
    store.activate_session(second, "second-upstream", "second-grant")
    assert store.stop_credentials(first)[0] == first_account
    assert store.stop_credentials(second)[0] == second_account
    assert store.revoke_session(first)
    assert store.stop_credentials(second)[2:] == ("second-upstream", "second-grant")
    store.close()


def test_generic_session_expiry_is_enforced(tmp_path):
    current = [datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)]
    store = OnboardingStore(str(tmp_path / "onboarding.sqlite3"), now=lambda: current[0])
    token, _ = store.issue_session()
    current[0] = datetime(2026, 10, 5, 12, 21, tzinfo=timezone.utc)
    with pytest.raises(PermissionError, match="invalid_or_expired"):
        store.claim_session(token, "first@instinct.example")
    store.close()
