import os
import sys
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SDK_ROOT / "src"))
sys.path.insert(0, str(SDK_ROOT / "fixtures"))

# Ensure scanner master path is reachable for tests that exercise the full
# pipeline. Tests skip cleanly if absent (so the repo stays runnable on
# machines without the master snapshot mounted).
DEFAULT_SCANNER_MASTER = "/tmp/are-scanner-master-d801736"
DEFAULT_SCAN_BIN = f"{DEFAULT_SCANNER_MASTER}/bin/are-scan"
if os.path.exists(DEFAULT_SCAN_BIN):
    os.environ.setdefault("ARE_SCAN_BIN", DEFAULT_SCAN_BIN)
