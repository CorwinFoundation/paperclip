#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    db = Path(os.environ.get("BACKBOND_SCAN_LOG_DB", "/var/lib/are-scanner/scan_log.sqlite3"))
    tenant = _required_env("BACKBOND_ACCEPTANCE_TENANT")
    agent = _required_env("BACKBOND_ACCEPTANCE_AGENT")
    endpoint = _required_env("BACKBOND_ACCEPTANCE_ENDPOINT")
    scanner_version = _required_env("BACKBOND_ACCEPTANCE_SCANNER_VERSION")
    corpus_version = _required_env("BACKBOND_ACCEPTANCE_CORPUS_VERSION")
    corpus_snapshot_hash = _required_env("BACKBOND_ACCEPTANCE_CORPUS_SNAPSHOT_HASH")
    seed = _required_env("BACKBOND_ACCEPTANCE_SEED")
    credential_ref = os.environ.get(
        "BACKBOND_ACCEPTANCE_CREDENTIAL_REF", "deploy-acceptance.v1"
    )
    base_url = os.environ.get("BACKBOND_ACCEPTANCE_BASE_URL", "https://127.0.0.1")
    with tempfile.TemporaryDirectory(prefix="backbond-acceptance-") as tmp:
        out = Path(tmp) / "acceptance.json"
        command = [
            str(repo / "scripts/deploy/acceptance_checks.sh"), "--base-url", base_url,
            "--staging-tenant", tenant, "--staging-agent", agent,
            "--staging-endpoint", endpoint,
            "--staging-credential-ref", credential_ref,
            "--scanner-version", scanner_version,
            "--corpus-version", corpus_version,
            "--corpus-snapshot-hash", corpus_snapshot_hash,
            "--seed", seed,
            "--scan-log-db", str(db), "--admin-token-file", str(credentials / "acceptance-admin-token"),
            "--out", str(out),
        ]
        completed = subprocess.run(command, check=False, text=True, capture_output=True)
        summary = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"passed": False, "checks": {}}
        envelope_path = Path(f"{out}.envelope.json")
        envelope = json.loads(envelope_path.read_text(encoding="utf-8")) if envelope_path.exists() else {}
        scan_id = envelope.get("scan_id")
        pins = {}
        if db.exists():
            with sqlite3.connect(db) as connection:
                row = connection.execute(
                    "SELECT forensic_bundle_uri, snapshot_hash, probe_set_hash FROM scan_log WHERE scan_id=? AND agent_id=? LIMIT 1",
                    (scan_id, agent),
                ).fetchone()
            if row:
                pins = dict(zip(("forensic_bundle_uri", "snapshot_hash", "probe_set_hash"), row))
        components = [{"name": name, "passed": check.get("status") == "PASS"} for name, check in summary.get("checks", {}).items()]
        result = {
            "passed": completed.returncode == 0 and summary.get("passed") is True,
            "terminal_status": envelope.get("status"),
            "scan_log_pins": pins,
            "customer_envelope": {key: envelope.get(key) for key in ("scan_id", "scanner_version", "corpus_version")},
            "components": components,
        }
        print(json.dumps(result, sort_keys=True))
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
