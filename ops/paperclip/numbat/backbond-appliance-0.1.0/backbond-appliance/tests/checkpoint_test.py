from __future__ import annotations
import json, os, tempfile

from appliance.readers.codex_rollout import CodexRolloutReader


def line(command):
    return (json.dumps({"timestamp": "2026-09-05T00:00:00Z", "command": command}) + "\n").encode()


def native_line(event_type, *, nested=False, content=None):
    record = {"timestamp": "2026-09-05T00:00:00Z"}
    if nested:
        record["payload"] = {"hook_event_name": event_type}
    else:
        record["event_type"] = event_type
    if content is not None:
        record["content"] = content
    return (json.dumps(record) + "\n").encode()


def test_checkpoint_resume_rotation_truncation_and_partial_line():
    with tempfile.TemporaryDirectory() as root:
        state = os.path.join(root, "state")
        first = os.path.join(root, "rollout-001.jsonl")
        second = os.path.join(root, "rollout-002.jsonl")
        with open(first, "wb") as fh: fh.write(line("one") + line("two") + b'{"command":"partial')
        reader = CodexRolloutReader({"path": root, "tenant_id": "backbond"}); reader.configure_checkpoint(state)
        events, cp = reader.read_window(1)
        assert [e["command"] for e in events] == ["one"]
        assert events[0]["tenant_id"] == "backbond"
        assert not os.path.exists(reader._checkpoint_path)
        reader.commit(cp)

        reader = CodexRolloutReader({"path": root, "tenant_id": "backbond"}); reader.configure_checkpoint(state)
        events, cp = reader.read_window(5)
        assert [e["command"] for e in events] == ["two"]
        reader.commit(cp)
        with open(first, "ab") as fh: fh.write(b'"}\n')
        with open(second, "wb") as fh: fh.write(line("rotated"))
        events, cp = reader.read_window(5)
        assert [e["command"] for e in events] == ["partial", "rotated"]
        reader.commit(cp)

        with open(second, "wb") as fh: fh.write(line("after-truncate"))
        events, _ = reader.read_window(5)
        assert [e["command"] for e in events] == ["after-truncate"]


def test_metadata_only_native_lifecycle_mapping():
    native_types = ["session-start", "prompt-submit", "codex-pre-tool",
                    "codex-post-tool", "stop", "session-end"]
    with tempfile.TemporaryDirectory() as root:
        state = os.path.join(root, "state")
        rollout = os.path.join(root, "rollout-native.jsonl")
        with open(rollout, "wb") as fh:
            for index, event_type in enumerate(native_types):
                fh.write(native_line(event_type, nested=bool(index % 2)))
            # An allowlisted word in content is not a metadata classification.
            fh.write(native_line("unrelated", content="session-start"))
        reader = CodexRolloutReader({"path": root, "tenant_id": "backbond"})
        reader.configure_checkpoint(state)
        events, _ = reader.read_window(20)
        assert [event["event_type"] for event in events] == native_types
        assert all("command" not in event for event in events)
        assert len({event["id"] for event in events}) == len(events)


if __name__ == "__main__":
    test_checkpoint_resume_rotation_truncation_and_partial_line()
    print("checkpoint regression: PASS")


def _rollout_lines():
    rows = [
        {"type": "session_meta", "payload": {"id": "s1", "instructions": "SECRET-PROMPT"}},
        {"type": "turn_context", "payload": {"cwd": "/x"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "c1",
                                              "name": "web", "arguments": "{}"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "c2",
                                              "name": "exec", "input": "rm -rf SECRET-CMD"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                                              "output": "SECRET-OUTPUT"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": "SECRET-PROMPT"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                              "content": "SECRET-RESPONSE"}},
        {"type": "event_msg", "payload": {"type": "token_count"}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}},
        {"type": "event_msg", "payload": {"type": "turn_aborted", "turn_id": "t2"}},
    ]
    return "".join(json.dumps(dict(r, timestamp="2026-10-07T00:00:00Z")) + "\n" for r in rows)


def test_rollout_lifecycle_classes_are_metadata_only_and_cover_every_agent(tmp_path):
    agents = ["d8bbc3b5-185f-43ed-bff0-d00323ddbfa3", "88e68019-1cb6-4c4f-83e1-9fd340fb6821"]
    for a in agents:
        d = tmp_path / "companies" / "co" / "agents" / a / "codex-home" / "sessions"
        d.mkdir(parents=True)
        (d / "rollout-1.jsonl").write_text(_rollout_lines())
    reader = CodexRolloutReader({"path": str(tmp_path)})
    reader.configure_checkpoint(str(tmp_path / "state"))
    events, cp = reader.read_window(100)
    reader.commit(cp)
    assert {e["agent_id"] for e in events} == set(agents)
    types = [e["event_type"] for e in events if e["agent_id"] == agents[0]]
    assert types == ["session.started", "turn.started", "tool.invoked", "command.exec",
                     "tool.result", "assistant.output", "turn.completed", "turn.aborted"]
    for e in events:
        assert len(e["payload_sha256"]) == 64 and e["payload_bytes"] > 0
        blob = json.dumps({k: v for k, v in e.items() if k != "command"})
        assert "SECRET" not in blob
    # Appending to the lower-sorted agent after the cursor moved on is still read.
    d0 = tmp_path / "companies" / "co" / "agents" / agents[0] / "codex-home" / "sessions"
    with open(d0 / "rollout-1.jsonl", "a") as fh:
        fh.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    more, _ = reader.read_window(100)
    assert [(e["agent_id"], e["event_type"]) for e in more] == [(agents[0], "turn.started")]
