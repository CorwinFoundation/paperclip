from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from log_tracker.policy_lifecycle import (  # noqa: E402
    ALLOWED_POLICY_EVENT_KINDS,
    POLICY_EVENT_KIND_ALIASES,
    PolicyLifecycleError,
    SQLitePolicyLifecycleSink,
    emit_policy_lifecycle_event,
)


def _base(**overrides):
    body = {
        "policy_id": "pol-001",
        "agent_id": "agent-001",
        "customer_id": "cust-001",
        "event_t": "2026-06-17T10:00:00Z",
        "wording_version": "wording-v2.0",
        "redaction_class": {
            "policy_id": "clear",
            "agent_id": "clear",
            "customer_id": "hash",
            "metadata.cancel_reason": "redact-on-receipt",
        },
        "metadata": {"source": "synthetic-fixture"},
        "scan_id_priced": "11111111-1111-4111-8111-111111111111",
        "premium_band": "B",
    }
    body.update(overrides)
    return body


def test_event_kind_aliases_cover_policy_lifecycle_spec() -> None:
    assert ALLOWED_POLICY_EVENT_KINDS == {
        "bind",
        "renewal",
        "mid_term_rescore",
        "mid_term_reprice",
        "cancel",
        "non_renewal",
    }
    assert POLICY_EVENT_KIND_ALIASES["policy.bound"] == "bind"
    assert POLICY_EVENT_KIND_ALIASES["policy.renewed"] == "renewal"
    assert POLICY_EVENT_KIND_ALIASES["policy.mid_term_rescore"] == "mid_term_rescore"
    assert POLICY_EVENT_KIND_ALIASES["policy.mid_term_reprice"] == "mid_term_reprice"
    assert POLICY_EVENT_KIND_ALIASES["policy.cancelled"] == "cancel"
    assert POLICY_EVENT_KIND_ALIASES["policy.non_renewal"] == "non_renewal"


@pytest.mark.parametrize(
    "event_kind",
    [
        "policy.bound",
        "renewal",
        "mid_term_rescore",
        "mid_term_reprice",
        "cancel",
        "non_renewal",
    ],
)
def test_policy_lifecycle_event_writes_to_tracker(event_kind: str) -> None:
    sink = SQLitePolicyLifecycleSink()
    body = _base(event_kind=event_kind)
    if event_kind in {"cancel", "non_renewal"}:
        body["scan_id_priced"] = None

    stored = emit_policy_lifecycle_event(sink=sink, **body)

    expected_kind = POLICY_EVENT_KIND_ALIASES.get(event_kind, event_kind)
    assert stored["event_kind"] == expected_kind
    assert stored["policy_id"] == "pol-001"
    assert stored["agent_id"] == "agent-001"
    assert stored["customer_id"] == "cust-001"
    assert stored["wording_version"] == "wording-v2.0"
    assert stored["metadata"] == {"source": "synthetic-fixture"}
    assert stored["redaction_class"]["customer_id"] == "hash"


@pytest.mark.parametrize(
    "event_kind",
    ["bind", "renewal", "mid_term_rescore", "mid_term_reprice"],
)
def test_priced_lifecycle_events_require_scan_id_priced(event_kind: str) -> None:
    sink = SQLitePolicyLifecycleSink()
    with pytest.raises(PolicyLifecycleError, match="scan_id_priced_required"):
        emit_policy_lifecycle_event(
            sink=sink,
            **_base(event_kind=event_kind, scan_id_priced=None),
        )


@pytest.mark.parametrize("event_kind", ["cancel", "non_renewal"])
def test_cancel_and_non_renewal_may_omit_scan_id_priced(event_kind: str) -> None:
    sink = SQLitePolicyLifecycleSink()
    stored = emit_policy_lifecycle_event(
        sink=sink,
        **_base(event_kind=event_kind, scan_id_priced=None),
    )
    assert stored["scan_id_priced"] is None


def test_synthetic_fixture_has_no_disallowed_scan_id_priced_gaps() -> None:
    sink = SQLitePolicyLifecycleSink()
    for offset, event_kind in enumerate(
        ["bind", "renewal", "mid_term_rescore", "mid_term_reprice"]
    ):
        emit_policy_lifecycle_event(
            sink=sink,
            **_base(
                event_kind=event_kind,
                event_t=f"2026-06-17T10:0{offset}:00Z",
                scan_id_priced=f"11111111-1111-4111-8111-11111111111{offset}",
            ),
        )
    emit_policy_lifecycle_event(
        sink=sink,
        **_base(
            event_kind="cancel",
            event_t="2026-06-17T10:10:00Z",
            scan_id_priced=None,
            metadata={"cancel_reason": "customer-request"},
        ),
    )
    emit_policy_lifecycle_event(
        sink=sink,
        **_base(
            event_kind="non_renewal",
            event_t="2026-06-17T10:11:00Z",
            scan_id_priced=None,
            metadata={"non_renewal_reason": "manual-uw-review"},
        ),
    )

    assert sink.count_missing_pricing_scan_ids() == 0
    assert [row["event_kind"] for row in sink.list_policy_events("pol-001")] == [
        "bind",
        "renewal",
        "mid_term_rescore",
        "mid_term_reprice",
        "cancel",
        "non_renewal",
    ]


def test_invalid_redaction_class_rejected_before_write() -> None:
    sink = SQLitePolicyLifecycleSink()
    with pytest.raises(PolicyLifecycleError, match="redaction_class_invalid"):
        emit_policy_lifecycle_event(
            sink=sink,
            **_base(event_kind="bind", redaction_class={"customer_id": "raw"}),
        )
