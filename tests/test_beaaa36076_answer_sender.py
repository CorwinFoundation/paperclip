import json
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest

from stage_b_pilot.mailgun import MailgunCancellationReminderSender


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_sender_routes_demo_cancellation_reminder_only_to_registered_agent():
    sender = MailgunCancellationReminderSender(
        "https://api.mailgun.test/v3/demo/messages", "secret", "BackBond <checks@demo.test>"
    )
    relay = {
        "agent_recipient": "ia5p01@mail.instinct.com",
        "reference": "BB-INSTINCT-1",
        "reason": "answer_no",
    }
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response({"id": "<accepted@demo.test>"})) as opened:
        assert sender(relay) == "<accepted@demo.test>"
    posted = parse_qs(opened.call_args.args[0].data.decode())
    assert posted["to"] == ["ia5p01@mail.instinct.com"]
    assert posted["subject"] == ["BackBond cancellation reminder BB-INSTINCT-1"]
    assert posted["v:backbond-check-id"] == ["BB-INSTINCT-1"]
    assert "agent-reported DEMO observation" in posted["text"][0]


@pytest.mark.parametrize("payload", [{}, {"message": "Queued. Thank you."}, {"id": ""}])
def test_generic_success_without_message_id_is_not_acceptance(payload):
    sender = MailgunCancellationReminderSender(
        "https://api.mailgun.test/v3/demo/messages", "secret", "BackBond <checks@demo.test>"
    )
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response(payload)):
        with pytest.raises(RuntimeError, match="mailgun_acceptance_id_missing"):
            sender({"agent_recipient": "agent@example.test", "reference": "BB-1", "reason": "answer_missing"})
