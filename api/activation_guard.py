"""Cross-process admission guard for scanner activation.

Accepted scans hold a shared flock for their full lifetime.  The deploy helper
takes the exclusive side before changing activation state, which both drains
accepted work and closes the check-then-restart admission race.
"""
from __future__ import annotations

import fcntl
import os
import threading
from pathlib import Path
from typing import Any

ACTIVATION_LOCK_ENV = "BACKBOND_ACTIVATION_LOCK_PATH"
DEFAULT_ACTIVATION_LOCK_PATH = "/var/lib/backbond-deploy-agent/activation.lock"


class ActivationInProgress(Exception):
    """Admission is temporarily closed while a scanner activation runs."""


class ActivationLease:
    def __init__(self, delegate: Any = None, *, path: str | None = None) -> None:
        lock_path = Path(path or os.environ.get(ACTIVATION_LOCK_ENV, DEFAULT_ACTIVATION_LOCK_PATH))
        self._file = lock_path.open("rb")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file.close()
            raise ActivationInProgress from exc
        self._delegate = delegate
        self._released = False
        self._mutex = threading.Lock()

    def release(self) -> None:
        with self._mutex:
            if self._released:
                return
            self._released = True
            try:
                if self._delegate is not None:
                    self._delegate.release()
            finally:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
                self._file.close()

    def set_delegate(self, delegate: Any) -> "ActivationLease":
        """Attach the ordinary rate-limit lease while retaining this lock."""
        self._delegate = delegate
        return self
