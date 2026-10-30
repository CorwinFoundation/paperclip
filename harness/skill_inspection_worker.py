"""Isolated optional engine entry point; skill code is never imported."""
from __future__ import annotations

import importlib.metadata
import json
import sys

from harness.skill_inspection import ENGINE_REVISION, ENGINE_VERSION


def main():
    try:
        package = importlib.metadata.distribution("skillspector")
        origin = json.loads(package.read_text("direct_url.json") or "{}")
        if package.version != ENGINE_VERSION or origin.get("vcs_info", {}).get("commit_id") != ENGINE_REVISION:
            raise RuntimeError("Pinned SkillSpector source installation required")
    except (importlib.metadata.PackageNotFoundError, RuntimeError):
        return 2

    def deny(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            raise PermissionError(f"Advisory static inspection forbids {event}")

    sys.addaudithook(deny)
    from skillspector.cli_entry import app
    sys.argv = ["skillspector", "scan", sys.argv[1], "--no-llm", "--fail-on-incomplete", "--format", "json"]
    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
