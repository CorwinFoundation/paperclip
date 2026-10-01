import json
from unittest.mock import patch

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
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response({"id": "<accepted@demo.test>"})):
        assert sender({"email": "user@example.test", "check_id": "in_1"}, {"answer": "OK"}) == "<accepted@demo.test>"


@pytest.mark.parametrize("payload", [{}, {"message": "Queued. Thank you."}, {"id": ""}])
def test_generic_success_without_message_id_is_not_acceptance(payload):
    sender = MailgunAnswerSender(
        "https://api.mailgun.test/v3/demo/messages", "secret", "BackBond <checks@demo.test>"
    )
    with patch("stage_b_pilot.mailgun.urlopen", return_value=Response(payload)):
        with pytest.raises(RuntimeError, match="mailgun_acceptance_id_missing"):
            sender({"email": "user@example.test", "check_id": "in_1"}, {"answer": "STOP"})
