from __future__ import annotations
import copy, hashlib, json
import pytest
from appliance.batch import BatchBuilder, Signer, _canonical, verify_batch
from appliance.pipeline import evidence_hash
from appliance.replay_manifest import ReplayManifestError, build_replay_manifest


def _fixture(tmp_path):
    signer = Signer(str(tmp_path / "key.pem"), "key-1")
    builder = BatchBuilder("appliance-1", signer, str(tmp_path / "state"),
                           {"collector_version": "appliance-0.1.0", "scanner_version": "0.1"})
    raw = [{"id": f"evt-{i}", "timestamp": f"2026-10-08T00:00:0{i}Z",
            "type": "tool_call", "command": f"secret-{i}"} for i in range(3)]
    events = [{"event_id": item["id"], "ts": item["timestamp"], "action_class": "tool_call",
               "outcome": "ok", "evidence_hash": evidence_hash(item)} for item in raw]
    dets = [{"event_id": "evt-1", "rule_id": "coding.command", "severity": "high",
             "evidence_hash": events[1]["evidence_hash"]}]
    batches = builder.build(events, dets, {"start": "2026-10-08T00:00:00Z",
                                           "end": "2026-10-08T00:01:00Z"}, max_events=2)
    return signer, raw, batches


def test_deterministic_ordered_linked_and_content_free(tmp_path):
    signer, raw, batches = _fixture(tmp_path)
    manifest = build_replay_manifest(reversed(batches), signer.public_pem(), verify_batch)
    assert manifest == build_replay_manifest(batches, signer.public_pem(), verify_batch)
    assert [row["batch_seq"] for row in manifest["batches"]] == [1, 2]
    assert manifest["batches"][1]["prev_batch_hash"] == manifest["batches"][0]["batch_hash"]
    assert manifest["batches"][0]["events"][1]["determinations"][0]["rule_id"] == "coding.command"
    prohibited = {"prompt", "output", "transcript", "recipients", "command", "crm_record", "pii"}
    def field_names(value):
        if isinstance(value, dict):
            names = set(value)
            for child in value.values():
                names.update(field_names(child))
            return names
        if isinstance(value, list):
            names = set()
            for child in value:
                names.update(field_names(child))
            return names
        return set()
    assert not prohibited.intersection(field_names(manifest))
    blob = json.dumps(manifest)
    for item in raw:
        assert item["command"] not in blob
    unhashed = dict(manifest); digest = unhashed.pop("artifact_sha256")
    assert digest == "sha256:" + hashlib.sha256(_canonical(unhashed)).hexdigest()


def test_rejects_broken_chain(tmp_path):
    signer, _, batches = _fixture(tmp_path)
    broken = copy.deepcopy(batches); broken[1]["prev_batch_hash"] = "sha256:" + "0" * 64
    fields = ("batch_id", "appliance_id", "batch_seq", "prev_batch_hash", "window", "events",
              "determinations", "counts", "producer")
    payload = _canonical({key: broken[1][key] for key in fields})
    broken[1]["batch_hash"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    broken[1]["signature"] = signer.sign(payload)
    with pytest.raises(ReplayManifestError, match="broken_batch_chain"):
        build_replay_manifest(broken, signer.public_pem(), verify_batch)


def test_reviewer_detects_mutated_partner_transcript(tmp_path):
    signer, raw, batches = _fixture(tmp_path)
    row = build_replay_manifest(batches, signer.public_pem(), verify_batch)["batches"][0]["events"][0]
    retained = {item["id"]: item for item in raw}
    assert evidence_hash(retained[row["event_id"]]) == row["evidence_hash"]
    retained[row["event_id"]]["command"] = "mutated"
    assert evidence_hash(retained[row["event_id"]]) != row["evidence_hash"]
