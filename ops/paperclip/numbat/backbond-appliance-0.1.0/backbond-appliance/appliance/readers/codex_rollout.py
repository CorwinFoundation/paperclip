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
import json, os, glob, hashlib, tempfile
from typing import Iterator, Dict, Any, Optional, Tuple, List
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

    def _tenant_id(self, path: str) -> str:
        return str(self.config.get("tenant_id") or _tenant_from_path(path))

    def discover(self) -> bool:
        root = self._root()
        if not root or not os.path.isdir(root):
            return False
        return bool(glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True) or
                    glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))

    def configure_checkpoint(self, state_dir: str) -> None:
        self._checkpoint_path = os.path.join(state_dir, "codex-rollout.checkpoint.json")

    def _checkpoint(self) -> Dict[str, Any]:
        try:
            with open(self._checkpoint_path, encoding="utf-8") as fh:
                value = json.load(fh)
            if isinstance(value, dict) and isinstance(value.get("file"), str):
                return value
        except (AttributeError, OSError, ValueError):
            pass
        return {"file": "", "offset": 0}

    def commit(self, checkpoint: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self._checkpoint_path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".codex-rollout.", dir=os.path.dirname(self._checkpoint_path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(checkpoint, fh, sort_keys=True)
                fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, self._checkpoint_path)
        finally:
            try: os.unlink(tmp)
            except FileNotFoundError: pass

    def read_window(self, max_events: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Read one bounded window and leave an incomplete final line unconsumed."""
        root = self._root()
        files = sorted(glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True)
                       or glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
        saved = self._checkpoint()
        events: List[Dict[str, Any]] = []
        next_checkpoint = dict(saved)
        for fp in files:
            if saved["file"] and fp < saved["file"]:
                continue
            offset = int(saved.get("offset", 0)) if fp == saved["file"] else 0
            try:
                if offset > os.path.getsize(fp) or (offset and saved.get("guard") != self._guard(fp, offset)):
                    offset = 0
                with open(fp, "rb") as fh:
                    fh.seek(offset)
                    while len(events) < max_events:
                        start = fh.tell(); raw = fh.readline()
                        if not raw: break
                        if not raw.endswith(b"\n"):
                            break
                        next_checkpoint = {"file": fp, "offset": fh.tell(),
                                           "guard": self._guard(fp, fh.tell())}
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line.startswith("{"): continue
                        try: obj = json.loads(line)
                        except json.JSONDecodeError: continue
                        cmd = _find_command(obj)
                        if cmd: events.append(self._event(fp, start, obj, cmd))
                    if len(events) >= max_events:
                        return events, next_checkpoint
            except OSError:
                continue
        return events, next_checkpoint

    @staticmethod
    def _guard(fp: str, offset: int) -> str:
        with open(fp, "rb") as fh:
            start = max(0, offset - 64); fh.seek(start)
            return hashlib.sha256(fh.read(offset - start)).hexdigest()

    def _event(self, fp: str, position: int, obj: Dict[str, Any], cmd: str) -> Dict[str, Any]:
        eid = "codex-" + hashlib.sha256(f"{fp}:{position}".encode()).hexdigest()[:16]
        return {"type": "tool_call", "event_type": "command.exec", "id": eid,
                "tenant_id": self._tenant_id(fp), "agent_id": _agent_from_path(fp),
                "ts": obj.get("timestamp") or obj.get("ts") or "", "command": cmd}

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
                        yield self._event(fp, ln, obj, cmd)
            except OSError:
                continue
