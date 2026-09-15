from __future__ import annotations

import importlib.util
import json
import os
import stat
import time
from pathlib import Path

from scripts.scheduled_scan import (
    ConversationRecordingRunner,
    ScheduleConfig,
    _prepare_conversation_log,
)


class FixtureRunner:
    def model_id(self) -> str:
        return "fixture"

    def complete(self, prompt: str) -> str:
        return f"answer to {prompt}"


def config(root: Path, retention: int = 14) -> ScheduleConfig:
    return ScheduleConfig(
        "tenant", "endpoint", root, "scanner", "corpus", 0, 1, 30, 0,
        100, 30, "judge", conversation_retention_days=retention,
    )


def test_scan_time_output_is_private_and_backbond_count_parses_it(tmp_path):
    path = _prepare_conversation_log(config(tmp_path), tmp_path / "2026-09-15" / "zoo")
    runner = ConversationRecordingRunner(FixtureRunner(), path, target="rag")

    assert runner.complete("hello") == "answer to hello"
    record = json.loads(path.read_text())
    assert record["internal_conversation_key"].startswith("rag:")
    assert [message["role"] for message in record["messages"]] == ["user", "assistant"]
    assert all(message["ts"] for message in record["messages"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    source = Path(__file__).parents[1] / "tools" / "vendor-count" / "src" / "backbond_count.py"
    spec = importlib.util.spec_from_file_location("backbond_count_fixture", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    parsed = module.parse(path.read_text(), kind="generic")
    assert parsed == [[
        {"role": "user", "text": "hello", "retrieval": None},
        {"role": "assistant", "text": "answer to hello", "retrieval": None},
    ]]


def test_retention_removes_only_expired_private_conversation_logs(tmp_path):
    old = tmp_path / "2026-09-01" / "zoo" / "private-conversations.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("old\n")
    expired = time.time() - 3 * 86400
    os.utime(old, (expired, expired))

    current = _prepare_conversation_log(config(tmp_path, retention=2), tmp_path / "2026-09-15" / "zoo")

    assert not old.exists()
    assert current.exists()
