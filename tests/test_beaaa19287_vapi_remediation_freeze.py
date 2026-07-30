import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "build_beaaa19287_vapi_remediation_freeze.py"
)
SPEC = importlib.util.spec_from_file_location("vapi_remediation_freeze", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_terminal_and_full_registry_accounting():
    summary, records = MODULE.build(MODULE.DEFAULT_SOURCE)
    assert summary["terminal_accounting_477"] == {
        "faithfully_materialized_current_text_transport": 290,
        "requires_different_capable_adapter": 187,
    }
    assert summary["full_registry_accounting_2000"] == {
        "already_authorized": 405,
        "destructive_tool_cross_session_cross_customer": 596,
        "faithfully_materialized_current_text_transport": 290,
        "requires_different_capable_adapter": 187,
        "requires_scope_expansion": 522,
    }
    assert len(records) == 477
    assert len({record["stable_case_id"] for record in records}) == 477
    assert all(record["source_row_sha256"] for record in records)


def test_materialized_content_is_byte_bound_and_adapter_cases_fail_closed():
    _, records = MODULE.build(MODULE.DEFAULT_SOURCE)
    for record in records:
        manifest = record["transport_manifest"]
        if manifest is None:
            assert record["route"]["status"] == "fail_closed"
            assert record["required_adapter"]["authorization_inference_permitted"] is False
            continue
        assert record["route"]["probe_requests_emitted"] == 0
        for branch in manifest["branches"]:
            for step in branch["steps"]:
                assert (
                    MODULE.sha256_bytes(step["content"].encode("utf-8"))
                    == step["content_utf8_sha256"]
                )


def test_five_derivations_are_byte_identical(tmp_path):
    hashes = []
    for index in range(5):
        freeze, summary = MODULE.write_artifacts(
            MODULE.DEFAULT_SOURCE, tmp_path / str(index)
        )
        hashes.append((MODULE.sha256_file(freeze), MODULE.sha256_file(summary)))
    assert len(set(hashes)) == 1
