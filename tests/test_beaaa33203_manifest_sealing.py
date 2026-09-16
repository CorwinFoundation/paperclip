import json

from api.bundle import sign_manifest
from api.full_scan_executor import manifest_content_hash, seal_manifest
from scripts.regrade_beaaa33199 import regrade


def test_seal_manifest_replaces_stale_integrity_fields(monkeypatch):
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    source = {
        "acceptance_gate": {"passed": True},
        "manifest_hash": "sha256:" + "0" * 64,
        "signed_manifest": "hmac-sha256:" + "0" * 64,
    }

    sealed = seal_manifest(source)
    unsigned = {
        key: value
        for key, value in sealed.items()
        if key not in {"signed_manifest", "manifest_hash"}
    }

    assert sealed["manifest_hash"] == manifest_content_hash(sealed)
    assert sealed["signed_manifest"] == sign_manifest(unsigned)
    assert sealed["manifest_hash"] != source["manifest_hash"]
    assert sealed["signed_manifest"] != source["signed_manifest"]


def test_regrade_emits_a_self_consistent_signed_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    source = tmp_path / "source.json"
    destination = tmp_path / "regraded.json"
    census = tmp_path / "census.json"
    source.write_text(json.dumps({
        "evidence_rows": [{
            "probe_id": "p1",
            "status": "incomplete",
            "incomplete_reason": "unavailable_capability:tool_calling=false",
            # BEAAA-33252: the row measured the target, so the regraded manifest
            # can pass the evidenced leg of the acceptance gate; and `expected`
            # is declared, because the gate no longer infers it from the row
            # count.
            "target_calls": 1,
            "acquisition_basis": "target-measured",
        }],
        "counts": {"expected": 1, "missing_required": 1},
        "signed_manifest": "stale",
        "manifest_hash": "stale",
    }))
    census.write_text(json.dumps({"capabilities": ["tool_calling"]}))

    assert regrade(source, destination, census) == (1, 0)
    manifest = json.loads(destination.read_text())
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key not in {"signed_manifest", "manifest_hash"}
    }
    assert manifest["acceptance_gate"]["passed"] is True
    assert manifest["manifest_hash"] == manifest_content_hash(manifest)
    assert manifest["signed_manifest"] == sign_manifest(unsigned)
