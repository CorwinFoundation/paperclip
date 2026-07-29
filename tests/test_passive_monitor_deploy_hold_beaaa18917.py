from __future__ import annotations

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "passive-monitor-demo.yml"


def _deploy_admission() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["deploy"]["if"]


def _admitted(*, cd_enabled: str, deploy_hold: str, event_name: str) -> bool:
    """Evaluate the deliberately small deploy admission expression."""
    expression = _deploy_admission()
    expected = (
        "vars.PASSIVE_DEMO_CD_ENABLED == 'true' "
        "&& vars.PASSIVE_DEMO_DEPLOY_HOLD == 'inactive' "
        "&& (github.event_name == 'push' "
        "|| github.event_name == 'workflow_dispatch')"
    )
    assert expression == expected
    return (
        cd_enabled == "true"
        and deploy_hold == "inactive"
        and event_name in {"push", "workflow_dispatch"}
    )


def test_active_hold_terminally_skips_deploy_before_steps_start() -> None:
    assert not _admitted(
        cd_enabled="true",
        deploy_hold="active",
        event_name="push",
    )


def test_missing_hold_state_fails_closed() -> None:
    assert not _admitted(
        cd_enabled="true",
        deploy_hold="",
        event_name="workflow_dispatch",
    )


def test_inactive_hold_still_requires_cd_enablement_and_deploy_event() -> None:
    assert _admitted(
        cd_enabled="true",
        deploy_hold="inactive",
        event_name="push",
    )
    assert not _admitted(
        cd_enabled="false",
        deploy_hold="inactive",
        event_name="push",
    )
    assert not _admitted(
        cd_enabled="true",
        deploy_hold="inactive",
        event_name="pull_request",
    )
