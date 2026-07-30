import json
import socket

from scripts.verify_beaaa19389_vapi_saved_scoring import build_receipt, canonical_bytes, sha256_bytes


def test_every_saved_deterministic_row_has_one_immutable_offline_verdict(monkeypatch):
    def forbid_network(*_args, **_kwargs):
        raise AssertionError("BEAAA-19389 verification must not make a network request")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", forbid_network)

    first = build_receipt()
    second = build_receipt()
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first["vapi_requests"] == 0
    assert first["counts"] == {
        "frozen_registry_rows": 2000,
        "eligible_saved_deterministic_rows": 897,
        "immutable_verdicts": 897,
        "verdicts": {"FAIL": 48, "INVALID": 642, "PASS": 207},
    }

    rows = first["rows"]
    assert len(rows) == len({row["row_id"] for row in rows}) == 897
    required = {
        "row_id", "response_sha256", "verifier_family", "verifier_version",
        "verdict", "reason", "evidence_pointer", "immutable_verdict_sha256",
    }
    for row in rows:
        assert set(row) == required
        assert row["response_sha256"]
        assert all(len(value) == 64 for value in row["response_sha256"])
        assert row["verifier_family"]
        assert row["verifier_version"] == "1.0.0"
        assert row["verdict"] in {"PASS", "FAIL", "INVALID"}
        assert row["reason"]
        assert row["evidence_pointer"].endswith("#records/" + row["row_id"])
        assert len(row["immutable_verdict_sha256"]) == 64

    assert sha256_bytes(canonical_bytes(first)) == sha256_bytes(canonical_bytes(second))
