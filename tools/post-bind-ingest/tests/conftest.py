"""pytest config: put the package src AND the tests directory on PYTHONPATH."""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
for _entry in (_PACKAGE_ROOT / "src", Path(__file__).resolve().parent):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))
