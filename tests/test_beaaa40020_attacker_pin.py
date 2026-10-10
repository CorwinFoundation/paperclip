"""BEAAA-40020: the live attacker pin is immutable and priced conservatively."""

from decimal import Decimal

from api.attacker_pins import ATTACKER_PINS
from tools.attacker_v1_live_campaign import ATTACKER_MODEL, MODEL_PRICES


def test_live_attacker_pin_matches_the_garak_provider_pin():
    expected = "qwen/qwen3.7-plus-20260602"
    assert ATTACKER_MODEL == expected
    assert ATTACKER_PINS["goat"]["red_team_model_name"] == expected
    assert ATTACKER_PINS["agent_breaker"]["red_team_model_name"] == expected


def test_live_attacker_price_uses_the_current_provider_rates():
    assert MODEL_PRICES[ATTACKER_MODEL] == (Decimal("0.32"), Decimal("1.28"))
