from __future__ import annotations

import json

import pytest

from harness import lane_c_capture as capture


def test_missing_target_configuration_fails_closed(monkeypatch):
    for name in ("SCANNER_TARGET_URL", "SCANNER_TARGET_MODEL", "SCANNER_TARGET_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(capture.CaptureError, match="missing production-like target"):
        capture._config()


def test_non_json_target_response_fails_closed():
    with pytest.raises(capture.CaptureError, match="did not contain a JSON object"):
        capture._json_object("placeholder target response")


def test_existing_evidence_binding_mismatch_fails_closed(tmp_path):
    path = tmp_path / "seed-0.json"
    path.write_text(json.dumps({"candidate_sha": "wrong"}))
    with pytest.raises(capture.CaptureError, match="binding mismatch"):
        capture._existing_run(path, "human_escalation_flagging", "esc-01", 0)


def test_exclusive_writer_refuses_overwrite(tmp_path):
    path = tmp_path / "record.json"
    capture._exclusive_json(path, {"verdict": "PASS"})
    with pytest.raises(capture.CaptureError, match="refusing to overwrite"):
        capture._exclusive_json(path, {"verdict": "FAIL"})
