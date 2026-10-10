"""Scanner-owned file-write outbound surface for Full Scan E."""
from __future__ import annotations

from typing import Any, Mapping

from harness.mock_tools.base import Operation, SandboxTool
from harness.mock_tools.interface import Impact


class FileWriteTool(SandboxTool):
    surface = "file_write"
    operations = {
        "write_file": Operation("write_file", Impact.HIGH, "file:write", "other"),
    }

    def _seed_state(self) -> None:
        self.state["files"] = {}

    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        path = str(params.get("path") or "")
        content = params.get("content") if params.get("content") is not None else ""
        self.state["files"][path] = {"content": content, "committed": True}
        return {"written_to_sandbox": True, "path": path, "committed": True}
