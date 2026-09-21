import signal

import pytest

from harness.runner import RunnerError
from scripts.path_b_endpoints import _syllable_scan_deadline


class _Runner:
    deadline = None

    def set_scan_deadline(self, deadline):
        self.deadline = deadline


def test_syllable_scan_deadline_interrupts_stalled_transport(monkeypatch):
    runner = _Runner()
    installed = {}
    timers = []

    monkeypatch.setattr("scripts.path_b_endpoints.time.monotonic", lambda: 100.0)
    monkeypatch.setattr(signal, "getsignal", lambda _sig: "previous-handler")
    monkeypatch.setattr(signal, "getitimer", lambda _which: (0.0, 0.0))
    monkeypatch.setattr(
        signal, "signal", lambda sig, handler: installed.__setitem__(sig, handler)
    )
    monkeypatch.setattr(
        signal,
        "setitimer",
        lambda which, *values: timers.append((which, values)) or (0.0, 0.0),
    )

    with pytest.raises(RunnerError, match="deadline exceeded after 7 seconds"):
        with _syllable_scan_deadline(runner, 7.0):
            installed[signal.SIGALRM](signal.SIGALRM, None)

    assert runner.deadline == 107.0
    assert timers == [
        (signal.ITIMER_REAL, (7.0,)),
        (signal.ITIMER_REAL, (0.0, 0.0)),
    ]
    assert installed[signal.SIGALRM] == "previous-handler"


def test_syllable_scan_deadline_rejects_non_positive_budget():
    with pytest.raises(ValueError, match="must be positive"):
        with _syllable_scan_deadline(_Runner(), 0):
            pass
