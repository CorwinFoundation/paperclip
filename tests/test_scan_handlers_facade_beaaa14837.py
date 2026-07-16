"""BEAAA-14837 compatibility-facade and extraction sentries."""
from api import scan_handlers
from api import scan_request_validation, scan_response_envelopes, scan_side_effects


def test_validation_helpers_remain_available_from_compatibility_facade():
    assert scan_handlers._require is scan_request_validation.require
    assert scan_handlers._validate_scan_mode is scan_request_validation.validate_scan_mode
    assert scan_handlers._validate_k_replicates is scan_request_validation.validate_k_replicates
    assert scan_handlers._validate_credential_ref is scan_request_validation.validate_credential_ref
    assert scan_handlers._validate_corpus_snapshot_hash is scan_request_validation.validate_corpus_snapshot_hash
    assert scan_handlers._validate_agent_endpoint is scan_request_validation.validate_agent_endpoint


def test_side_effect_helper_remains_available_from_compatibility_facade():
    assert scan_handlers._fire_webhook is scan_side_effects.fire_webhook


def test_response_helpers_remain_available_from_compatibility_facade():
    assert scan_handlers._scan_result_to_dict is scan_response_envelopes.scan_result_to_dict
    assert scan_handlers._uw_payload is scan_response_envelopes.uw_payload
    assert scan_handlers._customer_payload is scan_response_envelopes.customer_payload


def test_public_handler_surface_is_unchanged():
    assert scan_handlers.__all__ == [
        "PRIOR_SCAN_REFS_MAX", "RESCAN_PURPOSES", "ScanRuntime", "ScoreEnvelopeFactory",
        "rescan_handler", "scan_handler", "scan_status_handler",
    ]
