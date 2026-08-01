"""Codex rollout reader — real coding-agent substrate (Paperclip fleet on AriClaw).

Reads Codex session rollout files (`.../codex-home/sessions/**/rollout-*.jsonl`, one
JSON object per line) and extracts executed-command events. TOLERANT by design:
real production logs are messy and the exact rollout schema varies, so it recursively
hunts for the executed-command string rather than assuming a fixed field.

Content (the full command, which can contain secrets/paths) is treated as content —
it goes into the evidence hash and is available to local rules, but is stripped before
emission by the same no-content redaction as every other reader.
"""
from __future__ import annotations
import json, os, glob, hashlib
from typing import Iterator, Dict, Any, Optional
from .base import Reader, register

_CMD_KEYS = ("command", "cmd", "observed_command", "input", "arguments", "argv", "script")
_TYPE_HINTS = ("command.exec", "exec", "shell", "local_shell", "function_call", "tool")


def _find_command(node: Any) -> Optional[str]:
    """Recursively find the most command-like string in a rollout line."""
    if isinstance(node, dict):
        for k in _CMD_KEYS:
            if k in node:
                v = node[k]
                if isinstance(v, str) and v.strip():
                    return v
                if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                    return " ".join(v)
        for v in node.values():
            found = _find_command(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_command(v)
            if found:
                return found
    return None


def _agent_from_path(path: str) -> str:
    # .../companies/<uuid>/agents/<agent-uuid>/codex-home/sessions/...
    parts = path.replace("\\", "/").split("/")
    if "agents" in parts:
        i = parts.index("agents")
        if i + 1 < len(parts):
            return parts[i + 1][:12]
    return "company-level"


def _tenant_from_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if "companies" in parts:
        i = parts.index("companies")
        if i + 1 < len(parts):
            return parts[i + 1][:12]
    return "unknown"


@register("codex_rollout")
class CodexRolloutReader(Reader):
    def _root(self) -> str:
        return os.path.expanduser(self.config.get("path", ""))

    def discover(self) -> bool:
        root = self._root()
        if not root or not os.path.isdir(root):
            return False
        return bool(glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True) or
                    glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))

    def read(self) -> Iterator[Dict[str, Any]]:
        root = self._root()
        files = glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True) \
            or glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
        for fp in sorted(files):
            agent = _agent_from_path(fp)
            tenant = _tenant_from_path(fp)
            try:
                with open(fp, "r", errors="replace") as fh:
                    for ln, line in enumerate(fh):
                        line = line.strip()
                        if not line or not line.startswith("{"):
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        cmd = _find_command(obj)
                        if not cmd:
                            continue
                        ts = obj.get("timestamp") or obj.get("ts") or ""
                        eid = "codex-" + hashlib.sha256(f"{fp}:{ln}".encode()).hexdigest()[:16]
                        yield {
                            "type": "tool_call",
                            "event_type": "command.exec",
                            "id": eid,
                            "tenant_id": tenant,
                            "agent_id": agent,
                            "ts": ts,
                            "command": cmd,          # CONTENT — hashed + local-only, never emitted
                        }
            except OSError:
                continue
