from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import re
import threading
from urllib.request import urlopen

import pytest

from stage_b_pilot.onboarding import OnboardingStore
from stage_b_pilot.receipt_page import (
    agent_enrollment_instructions,
    enrollment_instruction,
    render_onboarding,
    make_handler,
)


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
    assert "BackBond fictional test. Everything in it is made up." in page
    assert "Stop BackBond" in page
    assert "https://scanner.backbond.ai/stage-b-demo/onboard/enroll/secret-session" in page
    assert "https://scanner.backbond.ai/stage-b-demo/onboard/stop/secret-session" in page
    assert "/onboard/session/" not in page
    assert "X-Instinct-Sender" in page
    for forbidden in ('<form', 'type="email"', "sign-in", "Onboarding history"):
        assert forbidden not in page
    assert "DEMO PRIVATE" in page


def test_agent_page_carries_protocol_while_preview_is_read_only(tmp_path):
    store = OnboardingStore(str(tmp_path / "onboarding.sqlite3"))
    token, account = store.issue_session()

    assert store.session_is_readable(token)
    assert store.session_is_readable(token)
    assert store.claim_session(token, "first@instinct.example") == account

    instructions = agent_enrollment_instructions(
        token, "https://scanner.backbond.ai/stage-b-demo"
    )
    assert f"/onboard/enroll/{token}" in instructions
    assert f"/onboard/stop/{token}" in instructions
    assert "X-Instinct-Sender" in instructions
    assert "opening or previewing it does not enroll" in instructions
    assert "fictional synthetic test tasks only" in instructions
    assert "stable unique task ID" in instructions
    store.close()


def test_unique_instruction_get_is_repeatable_and_has_no_claim_side_effect(tmp_path):
    database = str(tmp_path / "onboarding.sqlite3")
    handler = make_handler(
        "127.0.0.1", 1, "x" * 32, onboarding_db=database,
        inbound_db=str(tmp_path / "inbound.sqlite3"),
        public_base="http://127.0.0.1:PORT/stage-b-demo",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.RequestHandlerClass = make_handler(
        "127.0.0.1", 1, "x" * 32, onboarding_db=database,
        inbound_db=str(tmp_path / "inbound.sqlite3"),
        public_base=f"http://127.0.0.1:{server.server_port}/stage-b-demo",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/stage-b-demo/onboard") as response:
            page = response.read().decode()
        enroll = re.search(r"http://[^\s<]+/onboard/enroll/[A-Za-z0-9_-]+", page).group(0)
        link = enroll.replace("/onboard/enroll/", "/onboard/session/")
        for _ in range(2):
            with urlopen(link) as response:
                instructions = response.read().decode()
                assert response.headers.get_content_type() == "text/plain"
                assert "opening or previewing it does not enroll" in instructions
        token = link.rsplit("/", 1)[-1]
        store = OnboardingStore(database)
        assert store.claim_session(token, "preview-safe@instinct.example")
        store.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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
