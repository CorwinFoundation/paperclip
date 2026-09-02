#!/usr/bin/env python3
"""Read-only inventory of legacy scan_log rows containing _phase1_pending."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report legacy _phase1_pending scan_log rows without modifying them."
    )
    parser.add_argument("database", type=Path, help="Path to scan_log.sqlite3")
    args = parser.parse_args()

    database = args.database.resolve()
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT tenant_id, agent_id, run_timestamp, scan_id "
            "FROM scan_log WHERE score_json LIKE ? "
            "ORDER BY tenant_id, agent_id, run_timestamp",
            ('%"_phase1_pending"%',),
        ).fetchall()
    finally:
        connection.close()

    print(json.dumps({"affected_count": len(rows), "rows": [dict(row) for row in rows]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
