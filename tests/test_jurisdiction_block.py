import pytest

from harness.regulatory_controls import BlockedJurisdictionError, RegulatoryProbeTarget


def test_blocked_geo_request_refused() -> None:
    target = RegulatoryProbeTarget(blocked_jurisdictions={"XY", "ZZ"})

    with pytest.raises(BlockedJurisdictionError, match="XY.*blocked by policy"):
        target.handle_request("xy", {"operation": "scan", "subject_id": "subject-1"})

    assert target.handle_request("US", {"operation": "scan"}) == {
        "status": "accepted",
        "payload": {"operation": "scan"},
    }
