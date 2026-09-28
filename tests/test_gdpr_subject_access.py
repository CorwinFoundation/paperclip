from harness.regulatory_controls import RegulatoryProbeTarget, SubjectRecord


def test_data_subject_export_completeness() -> None:
    target = RegulatoryProbeTarget(
        records=[
            SubjectRecord("subject-1", "profile", {"email": "one@example.test"}),
            SubjectRecord("subject-2", "profile", {"email": "two@example.test"}),
        ],
        retained_artifacts=[
            SubjectRecord("subject-1", "telemetry", {"event": "tool_call"}),
            SubjectRecord("subject-1", "scan_evidence", {"result": "refused"}),
            SubjectRecord("subject-2", "telemetry", {"event": "login"}),
        ],
    )

    exported = target.export_subject("subject-1")

    assert exported == {
        "primary_records": [
            {"category": "profile", "value": {"email": "one@example.test"}}
        ],
        "retained_artifacts": [
            {"category": "telemetry", "value": {"event": "tool_call"}},
            {"category": "scan_evidence", "value": {"result": "refused"}},
        ],
    }
    assert "two@example.test" not in repr(exported)
