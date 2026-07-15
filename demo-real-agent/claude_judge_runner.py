"""Container-side runner for the host-only Claude Code judge bridge."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import struct
from typing import Any, Callable, Mapping

from harness.runner import ModelRunner, RunnerError


MAX_FRAME_BYTES = 512 * 1024
DEFAULT_SOCKET_PATH = "/run/claude-judge/judge.sock"


def _recv_exact(stream: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            raise RunnerError("claude judge proxy closed an incomplete response")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(stream: socket.socket, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME_BYTES:
        raise RunnerError("claude judge proxy request exceeds frame limit")
    stream.sendall(struct.pack("!I", len(encoded)) + encoded)


def receive_frame(stream: socket.socket) -> dict[str, Any]:
    length = struct.unpack("!I", _recv_exact(stream, 4))[0]
    if length <= 0 or length > MAX_FRAME_BYTES:
        raise RunnerError("claude judge proxy returned an invalid frame length")
    try:
        payload = json.loads(_recv_exact(stream, length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("claude judge proxy returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RunnerError("claude judge proxy returned a non-object response")
    return payload


def proxy_request(
    payload: Mapping[str, Any],
    *,
    socket_path: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(timeout_seconds)
            stream.connect(socket_path)
            send_frame(stream, payload)
            return receive_frame(stream)
    except RunnerError:
        raise
    except (OSError, TimeoutError) as exc:
        raise RunnerError(
            f"claude judge proxy unavailable: {type(exc).__name__}"
        ) from exc


RequestFn = Callable[..., dict[str, Any]]


class ClaudeCodeProxyRunner(ModelRunner):
    """ModelRunner backed by Claude Code on a host-only Unix socket."""

    def __init__(
        self,
        *,
        socket_path: str = DEFAULT_SOCKET_PATH,
        token: str,
        model: str = "sonnet",
        timeout_seconds: float = 120.0,
        request_fn: RequestFn = proxy_request,
    ) -> None:
        if len(token) < 32:
            raise RunnerError("CLAUDE_JUDGE_PROXY_TOKEN must be at least 32 characters")
        self._socket_path = socket_path
        self._token = token
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._request_fn = request_fn

    def model_id(self) -> str:
        return f"claude-code-max:{self._model}"

    def complete(self, prompt: str) -> str:
        response = self._request_fn(
            {
                "op": "complete",
                "token": self._token,
                "model": self._model,
                "prompt": prompt,
            },
            socket_path=self._socket_path,
            timeout_seconds=self._timeout_seconds,
        )
        if response.get("ok") is not True:
            code = str(response.get("error_code") or "unknown")
            raise RunnerError(f"claude judge proxy rejected the call: {code}")
        result = response.get("result")
        if not isinstance(result, str) or not result.strip():
            raise RunnerError("claude judge proxy returned an empty result")
        return result


def runner_from_env() -> ClaudeCodeProxyRunner:
    return ClaudeCodeProxyRunner(
        socket_path=os.environ.get("CLAUDE_JUDGE_SOCKET", DEFAULT_SOCKET_PATH),
        token=os.environ.get("CLAUDE_JUDGE_PROXY_TOKEN", ""),
        model=os.environ.get("CLAUDE_JUDGE_MODEL", "sonnet"),
        timeout_seconds=float(os.environ.get("CLAUDE_JUDGE_TIMEOUT_SECONDS", "120")),
    )


def proxy_health_from_env() -> bool:
    runner = runner_from_env()
    response = proxy_request(
        {"op": "health", "token": runner._token},
        socket_path=runner._socket_path,
        timeout_seconds=min(5.0, runner._timeout_seconds),
    )
    return response.get("ok") is True and response.get("status") == "ready"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args()
    if not args.health_check:
        parser.error("only --health-check is supported")
    if not proxy_health_from_env():
        raise SystemExit("claude judge proxy is not ready")
    print("claude judge proxy: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ClaudeCodeProxyRunner",
    "DEFAULT_SOCKET_PATH",
    "MAX_FRAME_BYTES",
    "proxy_health_from_env",
    "proxy_request",
    "receive_frame",
    "runner_from_env",
    "send_frame",
]
