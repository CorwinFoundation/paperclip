import pytest

from rubric.behavioral_activation import activate_behavioral_evidence


def outputs():
    return {
        "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL": {"raw": 3.0},
        "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER": {"raw": 2.0},
        "P4.3": {"raw": 4.0}, "P4.6": {"raw": 3.0},
        "P1.1": {"raw": 2.5}, "P1.2": {"raw": 3.5},
    }


def test_legacy_self_hashed_receipt_is_rejected():
    with pytest.raises(TypeError, match="VerifiedBehavioralReceipt"):
        activate_behavioral_evidence(outputs(), [{"predicate": "stop_compliance.post_stop_action", "verdict": "fail"}])
