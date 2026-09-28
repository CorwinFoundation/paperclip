from harness.regulatory_controls import RegulatoryProbeTarget, SubjectRecord


def test_deletion_request_propagates_to_retained_artifacts() -> None:
    target = RegulatoryProbeTarget(
        records=[SubjectRecord("delete-me", "profile", {"name": "Ada"})],
        retained_artifacts=[
            SubjectRecord("delete-me", "telemetry", {"event": "prompt"}),
            SubjectRecord("keep-me", "telemetry", {"event": "response"}),
        ],
    )

    assert target.delete_subject("delete-me") == 2
    assert target.export_subject("delete-me") == {
        "primary_records": [],
        "retained_artifacts": [],
    }
    assert target.export_subject("keep-me")["retained_artifacts"] == [
        {"category": "telemetry", "value": {"event": "response"}}
    ]
