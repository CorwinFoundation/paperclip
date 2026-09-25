from harness.bench.pii_scan_time_verifier import normalize_label, verify_detected
from scripts.beaaa35939_pii_capture import map_rows, prompt_for


def test_strict_label_verifier():
    assert verify_detected("DETECTED")["passed"] is True
    assert verify_detected("detected.")["passed"] is True
    assert verify_detected("I think DETECTED")["passed"] is False
    assert normalize_label("NOT_DETECTED") == "NOT_DETECTED"


def test_all_twenty_rows_are_single_turn_and_concrete():
    rows = map_rows()
    assert len(rows) == 20
    assert len({row["dedupe_sha256"] for row in rows}) == 20
    assert all("TEXT:" in prompt_for(row) for row in rows)
