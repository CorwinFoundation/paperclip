"""BEAAA-14451 (F14) — default-deny startup gate for the real-agent demo.

``_authorized_for_run`` fails open when no ``DEMO_ACCESS_TOKEN`` is configured so
loopback development stays frictionless. That is only safe while the listener is
unreachable off-box, so any non-loopback bind (e.g. the container ``--host
0.0.0.0``) must fail closed at startup unless a strong token is configured.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


DEMO_DIR = Path(__file__).resolve().parents[1]
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

import app  # noqa: E402
from app import _require_token_for_nonloopback_bind, main  # noqa: E402


STRONG_TOKEN = "x" * 32


def test_nonloopback_bind_without_token_fails_closed(monkeypatch) -> None:
    # AC1 — DEMO_ENVIRONMENT unset/container, no token, 0.0.0.0 → exits.
    monkeypatch.delenv("DEMO_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DEMO_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit):
        _require_token_for_nonloopback_bind("0.0.0.0")


def test_nonloopback_bind_with_short_token_fails_closed(monkeypatch) -> None:
    # A configured-but-weak token is not enough for an exposed bind.
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "x" * 31)
    monkeypatch.delenv("DEMO_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit):
        _require_token_for_nonloopback_bind("0.0.0.0")


def test_nonloopback_bind_with_strong_token_is_allowed(monkeypatch) -> None:
    # AC2 — same exposed bind + 32+ char token → gate passes.
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", STRONG_TOKEN)
    monkeypatch.delenv("DEMO_ENVIRONMENT", raising=False)
    assert _require_token_for_nonloopback_bind("0.0.0.0") is None


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_bind_without_token_is_allowed(monkeypatch, host) -> None:
    # AC3 — loopback stays frictionless with no token configured.
    monkeypatch.delenv("DEMO_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DEMO_ENVIRONMENT", raising=False)
    assert _require_token_for_nonloopback_bind(host) is None


def test_main_exits_before_serving_on_exposed_bind(monkeypatch) -> None:
    # AC1 end-to-end — main() must fail closed *before* a socket is bound/served.
    monkeypatch.delenv("DEMO_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DEMO_ENVIRONMENT", raising=False)
    monkeypatch.setattr(sys, "argv", ["app", "--host", "0.0.0.0"])

    def _boom(*_args, **_kwargs):  # pragma: no cover - guard, must not run
        raise AssertionError("server constructed despite an exposed unauth bind")

    monkeypatch.setattr(app, "ThreadingHTTPServer", _boom)
    with pytest.raises(SystemExit):
        main()
