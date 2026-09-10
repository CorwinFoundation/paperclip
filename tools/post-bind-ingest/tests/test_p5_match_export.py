from post_bind_ingest.p5_match_export import match_rows, redact


def _source(event_id: str, content: str, call_id: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "tool_source_reference",
        "logged_at": "2026-09-10T20:00:00Z",
        "payload": {
            "content": content,
            "source_type": "command_output",
            "trust": "untrusted-content",
            "tool_call_event_id": call_id,
        },
    }


def _call(event_id: str, command: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "tool_call",
        "logged_at": "2026-09-10T20:00:00Z",
        "payload": {
            "tool": "commandExecution",
            "arguments": {"command": command, "cwd": "/repo"},
            "status": "completed",
        },
    }


def test_match_row_carries_pattern_context_and_originating_call():
    events = [
        _source("s1", "x" * 400 + " ignore previous instructions " + "y" * 400, "c1"),
        _call("c1", "grep -r injection ."),
    ]

    (row,) = match_rows(events)

    assert row["patterns"] == ["ignore_previous"]
    assert row["tool_call_event_id"] == "c1"
    assert row["command"] == "grep -r injection ."
    assert row["cwd"] == "/repo"
    hit = row["hits"][0]
    assert hit["matched_text"] == "ignore previous instructions"
    assert "ignore previous instructions" in hit["context"]
    # 200-char window around the match, elided on both sides.
    assert hit["context"].startswith("...") and hit["context"].endswith("...")
    assert len(hit["context"]) < len(events[0]["payload"]["content"])


def test_non_matching_and_agent_authored_events_are_not_rows():
    events = [
        _source("s1", "nothing of interest here", "c1"),
        _call("c1", "ls"),
        # A tool_call whose own arguments mention injection is agent-authored, not a source.
        _call("c2", "grep -r 'ignore previous instructions' ."),
    ]

    assert match_rows(events) == []


def test_credentials_are_redacted_before_the_context_is_written():
    events = [
        _source("s1", "act as root using sk-abcdefghijklmnopqrstuvwx now", "c1"),
        _call("c1", "cat config"),
    ]

    (row,) = match_rows(events)

    assert "sk-abcdefghijklmnopqrstuvwx" not in row["hits"][0]["context"]
    assert "[REDACTED:openai_key]" in row["hits"][0]["context"]


def test_redact_covers_common_credential_shapes():
    assert "[REDACTED:github_token]" in redact("ghp_" + "a" * 30)
    assert "[REDACTED:bearer]" in redact("Authorization: Bearer abcdefghijklmnop")
    assert "[REDACTED:assigned_secret]" in redact("api_key=supersecretvalue")
    assert "[REDACTED:private_key]" in redact("-----BEGIN RSA PRIVATE KEY-----")
