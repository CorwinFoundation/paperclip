from __future__ import annotations
import json, os, tempfile

from appliance.readers.codex_rollout import CodexRolloutReader


def line(command):
    return (json.dumps({"timestamp": "2026-09-05T00:00:00Z", "command": command}) + "\n").encode()


def test_checkpoint_resume_rotation_truncation_and_partial_line():
    with tempfile.TemporaryDirectory() as root:
        state = os.path.join(root, "state")
        first = os.path.join(root, "rollout-001.jsonl")
        second = os.path.join(root, "rollout-002.jsonl")
        with open(first, "wb") as fh: fh.write(line("one") + line("two") + b'{"command":"partial')
        reader = CodexRolloutReader({"path": root}); reader.configure_checkpoint(state)
        events, cp = reader.read_window(1)
        assert [e["command"] for e in events] == ["one"]
        assert not os.path.exists(reader._checkpoint_path)
        reader.commit(cp)

        reader = CodexRolloutReader({"path": root}); reader.configure_checkpoint(state)
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


if __name__ == "__main__":
    test_checkpoint_resume_rotation_truncation_and_partial_line()
    print("checkpoint regression: PASS")
