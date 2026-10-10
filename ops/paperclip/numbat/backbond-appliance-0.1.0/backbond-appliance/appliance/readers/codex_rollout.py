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
import json, os, glob, hashlib, tempfile, time
from typing import Iterator, Dict, Any, Optional, Tuple, List
from .base import Reader, register

_CMD_KEYS = ("command", "cmd", "observed_command", "input", "arguments", "argv", "script")
_TYPE_HINTS = ("command.exec", "exec", "shell", "local_shell", "function_call", "tool")
_NATIVE_TYPE_KEYS = frozenset(("type", "event_type", "event", "name", "hook_event_name"))
_NATIVE_TYPES = frozenset((
    "session-start", "prompt-submit", "codex-pre-tool", "codex-post-tool",
    "stop", "session-end",
))


# Codex rollout records -> lifecycle classes. Keyed on (top-level type, payload.type).
# Only metadata is emitted for these: type, ids, timestamps, payload sha256/bytes.
_ROLLOUT_CLASSES = {
    ("session_meta", None): "session.started",
    ("event_msg", "task_started"): "turn.started",
    ("response_item", "function_call"): "tool.invoked",
    ("response_item", "custom_tool_call"): "tool.invoked",
    ("response_item", "local_shell_call"): "tool.invoked",
    ("response_item", "web_search_call"): "tool.invoked",
    ("response_item", "function_call_output"): "tool.result",
    ("response_item", "custom_tool_call_output"): "tool.result",
    ("response_item", "message"): "assistant.output",  # role == assistant only
    ("event_msg", "task_complete"): "turn.completed",
    ("event_msg", "turn_aborted"): "turn.aborted",
    ("event_msg", "error"): "turn.failed",
}
_ROLLOUT_TOP_TYPES = frozenset(("session_meta", "event_msg", "response_item", "turn_context",
                                "compacted", "world_state"))
_ID_KEYS = ("id", "call_id", "turn_id", "session_id")
_SHELL_TOOLS = frozenset(("shell", "exec", "exec_command", "local_shell", "unified_exec",
                          "container.exec", "shell_command"))


def _rollout_class(obj: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Return (is_rollout_record, lifecycle_class, payload) for a Codex rollout line."""
    top = obj.get("type")
    if top not in _ROLLOUT_TOP_TYPES:
        return False, None, {}
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    sub = payload.get("type") if isinstance(payload.get("type"), str) else None
    cls = _ROLLOUT_CLASSES.get((top, sub)) or _ROLLOUT_CLASSES.get((top, None))
    if cls == "assistant.output" and payload.get("role") != "assistant":
        cls = None
    return True, cls, payload


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


def _find_native_type(node: Any) -> Optional[str]:
    """Find only an explicitly pinned lifecycle value under a metadata key.

    Values under content/command fields are never considered. This keeps the
    tolerant schema walk from turning prompt text into an event classifier.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _NATIVE_TYPE_KEYS and isinstance(value, str) and value in _NATIVE_TYPES:
                return value
        for value in node.values():
            if isinstance(value, (dict, list)):
                found = _find_native_type(value)
                if found:
                    return found
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                found = _find_native_type(value)
                if found:
                    return found
    return None


def _agent_from_path(path: str) -> str:
    # .../companies/<uuid>/agents/<agent-uuid>/codex-home/sessions/...
    parts = path.replace("\\", "/").split("/")
    if "agents" in parts:
        i = parts.index("agents")
        if i + 1 < len(parts):
            return parts[i + 1]
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
            if isinstance(value, dict) and isinstance(value.get("files"), dict):
                return value
            if isinstance(value, dict) and isinstance(value.get("file"), str):
                # Legacy single-cursor checkpoint: keep its one position.
                files = {value["file"]: {"offset": int(value.get("offset", 0)),
                                         "guard": value.get("guard")}} if value["file"] else {}
                return {"files": files}
        except (AttributeError, OSError, ValueError):
            pass
        return {"files": {}}

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
        """Read one bounded window and leave an incomplete final line unconsumed.

        The checkpoint keeps one offset per file, so every agent's rollouts are
        followed. (A single global cursor skipped every path sorting before it.)
        Files never seen and older than `max_age_hours` are not backfilled.
        """
        root = self._root()
        files = sorted(glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True)
                       or glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
        horizon = time.time() - float(self.config.get("max_age_hours", 24)) * 3600
        saved = self._checkpoint()["files"]
        cursors: Dict[str, Any] = {}
        events: List[Dict[str, Any]] = []
        for fp in files:
            try:
                size, mtime = os.path.getsize(fp), os.path.getmtime(fp)
            except OSError:
                continue
            prior = saved.get(fp)
            if mtime < horizon:
                continue  # stale: drop from checkpoint, never backfill
            offset = int(prior.get("offset", 0)) if prior else 0
            if prior and offset == size:
                cursors[fp] = prior
                continue
            cursors[fp] = prior or {"offset": 0, "guard": None}
            if len(events) >= max_events:
                continue
            try:
                if offset > size or (offset and prior.get("guard") != self._guard(fp, offset)):
                    offset = 0
                with open(fp, "rb") as fh:
                    fh.seek(offset)
                    while len(events) < max_events:
                        start = fh.tell(); raw = fh.readline()
                        if not raw or not raw.endswith(b"\n"):
                            break
                        cursors[fp] = {"offset": fh.tell(), "guard": self._guard(fp, fh.tell())}
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line.startswith("{"): continue
                        try: obj = json.loads(line)
                        except json.JSONDecodeError: continue
                        if not isinstance(obj, dict): continue
                        event = self._classify(fp, start, obj, raw)
                        if event:
                            events.append(event)
            except OSError:
                continue
        return events, {"files": cursors}

    def _classify(self, fp: str, position: int, obj: Dict[str, Any],
                  raw: bytes) -> Optional[Dict[str, Any]]:
        is_rollout, cls, payload = _rollout_class(obj)
        if is_rollout:
            if cls is None:
                return None
            shell = payload.get("type") == "local_shell_call" or payload.get("name") in _SHELL_TOOLS
            cmd = _find_command(payload) if cls == "tool.invoked" and shell else None
            # Shell/exec tool calls stay command.exec so the rule pack still sees them.
            event = self._event(fp, position, obj, cmd, "command.exec" if cmd else cls)
            event["source_record_type"] = obj.get("type") + (
                "/" + payload["type"] if isinstance(payload.get("type"), str) else "")
            event["payload_sha256"] = hashlib.sha256(raw.rstrip(b"\n")).hexdigest()
            event["payload_bytes"] = len(raw.rstrip(b"\n"))
            for k in _ID_KEYS:
                if isinstance(payload.get(k), str):
                    event["source_" + k] = payload[k]
            return event
        cmd = _find_command(obj)
        native_type = _find_native_type(obj)
        if cmd or native_type:
            return self._event(fp, position, obj, cmd, native_type)
        return None

    @staticmethod
    def _guard(fp: str, offset: int) -> str:
        with open(fp, "rb") as fh:
            start = max(0, offset - 64); fh.seek(start)
            return hashlib.sha256(fh.read(offset - start)).hexdigest()

    def _event(self, fp: str, position: int, obj: Dict[str, Any], cmd: Optional[str],
               native_type: Optional[str]) -> Dict[str, Any]:
        eid = "codex-" + hashlib.sha256(f"{fp}:{position}".encode()).hexdigest()[:16]
        event_type = native_type or "command.exec"
        event = {"type": event_type, "event_type": event_type, "id": eid,
                 "tenant_id": self._tenant_id(fp), "agent_id": _agent_from_path(fp),
                 "ts": obj.get("timestamp") or obj.get("ts") or ""}
        if cmd:
            event["command"] = cmd
        return event

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
                        native_type = _find_native_type(obj)
                        if not cmd and not native_type:
                            continue
                        eid = "codex-" + hashlib.sha256(f"{fp}:{ln}".encode()).hexdigest()[:16]
                        yield self._event(fp, ln, obj, cmd, native_type)
            except OSError:
                continue
