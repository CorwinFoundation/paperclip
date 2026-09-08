from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "agent-scan-friday.yml"


def test_agent_scan_friday_workflow_is_scheduled_and_scoped() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)

    assert workflow["on"]["schedule"] == [{"cron": "17 9 * * 5"}]
    assert workflow["permissions"] == {"contents": "write"}

    job = workflow["jobs"]["collect"]
    assert job["env"] == {
        "AGENT_SCAN_NPM_PACKAGE": "backbond",
        "AGENT_SCAN_GITHUB_REPO": "CorwinFoundation/are-scanner",
        "AGENT_SCAN_PAGE_URL": "https://backbond.ai/agent-scan",
        "GITHUB_TOKEN": "${{ github.token }}",
    }
    assert "--append backbond/growth/agent-scan-scoreboard.md" in text
    assert "--date" not in text
    assert "AGENT_SCAN_CDN_LOG" not in text
    assert "AGENT_SCAN_DEMO_PATH" not in text
    assert 'git add -- "${scoreboard}"' in text
    assert "git push origin HEAD:master" in text
