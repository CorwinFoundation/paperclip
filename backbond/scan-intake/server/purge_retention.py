#!/usr/bin/env python3
import os
from datetime import datetime, timedelta, timezone

from qa_service import IntakeStore

path = os.environ.get("QA_DB_PATH", "/var/lib/backbond-scan-intake/scan-intake.sqlite")
cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
print(f"deleted={IntakeStore(path).purge_before(cutoff)} cutoff={cutoff}")
