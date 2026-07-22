"""Controlled native worker cases for BEAAA-16616."""
from __future__ import annotations

from tools.framework_worker_registry import WORKERS
from tools.framework_workers import _agentdojo_adapter_result, _garak_adapter_result

def test_native_workers_return_terminal_incomplete_for_unsupported_target():
    for worker_id, adapter in (
        ("agentdojo", _agentdojo_adapter_result),
        ("garak", _garak_adapter_result),
    ):
        result = adapter(WORKERS[worker_id], {}, {"job_id": "scan"}, "local-target@1")
        assert result["status"] == "incomplete"
        assert result["evidence_ref"] is None
        assert result["incomplete_reason"] == f"worker_unsupported_interface:{worker_id}:missing_subject_adapter"
