from post_bind_ingest.deployment_reconciliation import resolve_deployment_limb


RECEIPT = {
    "tenant_id_hash": "tenant-hash",
    "binding_id_hash": "binding-hash",
    "agent_id_hash": "agent-hash",
}


def row(deployment_id="deploy-opaque"):
    return {**RECEIPT, "deployment_id": deployment_id}


def test_exact_registry_match_attaches_the_existing_opaque_deployment_id():
    joined = resolve_deployment_limb(RECEIPT, [row(), {**row("other"), "agent_id_hash": "other"}])
    assert joined.resolved is True
    assert joined.deployment_id == "deploy-opaque"
    assert joined.reason == "resolved"


def test_absent_registry_match_is_explicitly_unresolved():
    joined = resolve_deployment_limb(RECEIPT, [])
    assert joined.resolved is False
    assert joined.deployment_id is None
    assert joined.reason == "deployment_registry_miss"


def test_non_unique_registry_match_is_explicitly_unresolved_even_if_ids_repeat():
    joined = resolve_deployment_limb(RECEIPT, [row(), row()])
    assert joined.resolved is False
    assert joined.deployment_id is None
    assert joined.reason == "deployment_registry_non_unique"


def test_incomplete_receipt_context_never_falls_back_to_a_partial_match():
    joined = resolve_deployment_limb({**RECEIPT, "agent_id_hash": ""}, [row()])
    assert joined.resolved is False
    assert joined.deployment_id is None
    assert joined.reason == "incomplete_receipt_context"


def test_sender_or_source_fields_cannot_choose_the_deployment():
    joined = resolve_deployment_limb(
        {**RECEIPT, "deployment_id": "sender-choice", "source_id_hash": "source"},
        [row("registry-choice")],
    )
    assert joined.deployment_id == "registry-choice"
