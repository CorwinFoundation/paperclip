import json
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest

from stage_b_pilot.mailgun import MailgunAnswerSender


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_sender_returns_only_mailgun_provider_acceptance_id():
    sender = MailgunAnswerSender(
        "https://api.mailgun.test/v3/demo/messages", "secret", "BackBond <checks@demo.test>"
    )
    relay = {
        "agent_recipient": "ia5p01@mail.instinct.com",
        "email": "katzzzari@gmail.com",
        "recipient": "attacker@example.test",
        "answer": "OK",
        "reference": "BB-INSTINCT-1",
        "body_text": "Proposed action: Reserve one lamp\nPrice: $48.00\nDeposit: $0.00\nTerms: No substitutions",
    }
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response({"id": "<accepted@demo.test>"})) as opened:
        assert sender(relay) == "<accepted@demo.test>"
    posted = parse_qs(opened.call_args.args[0].data.decode())
    assert posted["to"] == ["ia5p01@mail.instinct.com"]
    assert "attacker@example.test" not in opened.call_args.args[0].data.decode()
    assert posted["subject"] == ["BackBond APPROVED BB-INSTINCT-1"]
    assert posted["v:backbond-check-id"] == ["BB-INSTINCT-1"]
    assert posted["text"] == ["BackBond DEMO decision: APPROVED\nCheck reference: BB-INSTINCT-1\nProposed action: Reserve one lamp\nPrice: $48.00\nMoney at risk: Unknown\nDeposit: $0.00\nTerms: No substitutions"]


@pytest.mark.parametrize("payload", [{}, {"message": "Queued. Thank you."}, {"id": ""}])
def test_generic_success_without_message_id_is_not_acceptance(payload):
    sender = MailgunAnswerSender(
        "https://api.mailgun.test/v3/demo/messages", "secret", "BackBond <checks@demo.test>"
    )
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response(payload)):
        with pytest.raises(RuntimeError, match="mailgun_acceptance_id_missing"):
            sender({"agent_recipient": "agent@example.test", "answer": "STOP", "reference": "BB-1", "body_text": "Proposed action: Demo"})


def test_missing_persisted_fields_are_unknown():
    sender = MailgunAnswerSender(
        "https://api.mailgun.test/v3/demo/messages", "secret", "BackBond <checks@demo.test>"
    )
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response({"id": "accepted"})) as opened:
        sender({"agent_recipient": "agent@example.test", "answer": "STOP", "reference": "BB-2", "body_text": "Proposed action: Cancel demo"})
    posted = parse_qs(opened.call_args.args[0].data.decode())
    assert posted["subject"] == ["BackBond STOP BB-2"]
    assert "Price: Unknown\nMoney at risk: Unknown\nDeposit: Unknown\nTerms: Unknown" in posted["text"][0]


def test_price_never_falls_back_to_money_at_risk():
    sender = MailgunAnswerSender(
        "https://api.mailgun.test/v3/demo/messages", "secret", "BackBond <checks@demo.test>"
    )
    relay = {"agent_recipient": "agent@example.test", "answer": "OK", "reference": "BB-3", "body_text": "Proposed action: Demo\nMoney at risk: $500.00"}
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response({"id": "accepted"})) as opened:
        sender(relay)
    posted = parse_qs(opened.call_args.args[0].data.decode())
    assert "Price: Unknown\nMoney at risk: $500.00" in posted["text"][0]
