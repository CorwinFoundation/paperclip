import os
import sqlite3
import subprocess
import textwrap
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "deploy" / "acceptance_checks.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip())
    path.chmod(0o755)


def test_acceptance_checks_fail_when_smoke_scan_never_reaches_terminal_status(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "acceptance.json"

    _write_executable(
        bin_dir / "sleep",
        """
        #!/usr/bin/env sh
        exit 0
        """,
    )
    _write_executable(
        bin_dir / "curl",
        """
        #!/usr/bin/env python3
        import json
        import sys

        args = sys.argv[1:]
        url = next((arg for arg in args if arg.startswith("http")), args[-1])

        if url.endswith("/scanner/v1/sla"):
            sys.stdout.write("200")
            raise SystemExit(0)

        if url.endswith("/scanner/v1/scans"):
            joined = " ".join(args)
            if "Bearer staged-admin" in joined:
                sys.stdout.write(json.dumps({"scan_id": "scan-stuck", "status": "accepted"}))
            else:
                sys.stdout.write("401")
            raise SystemExit(0)

        if "/scan/scan-stuck" in url:
            sys.stdout.write(json.dumps({"scan_id": "scan-stuck", "status": "accepted"}))
            raise SystemExit(0)

        sys.stdout.write("500")
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--base-url",
            "https://scanner.local",
            "--staging-tenant",
            "deploy-staging",
            "--staging-agent",
            "deploy-agent",
            "--staging-endpoint",
            "scanner.local",
            "--scanner-version",
            "0.5.0",
            "--corpus-version",
            "0.1.0",
            "--corpus-snapshot-hash",
            "sha256:" + "a" * 64,
            "--seed",
            "42",
            "--scan-log-db",
            str(tmp_path / "missing.sqlite3"),
            "--admin-token",
            "staged-admin",
            "--out",
            str(out),
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "smoke_scan FAILED" in result.stderr
    assert "final_status=accepted" in result.stderr
    assert '"passed": false' in out.read_text()


def test_stale_pinned_row_cannot_satisfy_new_acceptance_scan(tmp_path: Path) -> None:
    db = tmp_path / "scan.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE scan_log (scan_id TEXT, agent_id TEXT, run_timestamp TEXT, forensic_bundle_uri TEXT, snapshot_hash TEXT, probe_set_hash TEXT)")
        connection.execute("INSERT INTO scan_log VALUES (?,?,?,?,?,?)", ("scan-old", "deploy-agent", "2026-07-15", "bundle", "sha256:old", "sha256:old"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "sleep", "#!/usr/bin/env sh\nexit 0\n")
    _write_executable(bin_dir / "curl", """
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        args = sys.argv[1:]
        url = next((arg for arg in args if arg.startswith("http")), args[-1])
        joined = " ".join(args)
        if url.endswith("/scanner/v1/sla"): print("200", end="")
        elif url.endswith("/scanner/v1/scans"):
            request_arg = next((arg for arg in args if arg.startswith("@")), "")
            payload = json.loads(Path(request_arg[1:]).read_text()) if request_arg else {}
            valid = (
                payload.get("tenant_id") == "deploy-staging"
                and payload.get("agent_id") == "deploy-agent"
                and payload.get("agent_endpoint") == {"host": "scanner.local", "transport": "rest"}
                and payload.get("credential_ref") == "deploy-acceptance.v1"
                and bool(payload.get("credential_secret"))
                and payload.get("corpus_snapshot_hash") == "sha256:" + "a" * 64
                and payload.get("scanner_version") == "0.5.0"
                and payload.get("corpus_version") == "0.1.0"
                and payload.get("seed") == 42
                and payload.get("scan_tier") == "smoke"
            )
            print(json.dumps({"scan_id":"scan-new"}) if "Bearer staged-admin" in joined and valid else "401", end="")
        elif "/scan/scan-new" in url:
            print(json.dumps({"scan_id":"scan-new","status":"complete","scanner_version":"1.2.0","corpus_version":"v1","are_total":80,"subscores":{},"band":"B","confidence":1,"evidence_refs":[],"uncovered_dimensions":[]}), end="")
        else: print("500", end="")
    """)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    out = tmp_path / "acceptance.json"
    result = subprocess.run(["bash", str(SCRIPT), "--base-url", "https://scanner.local", "--staging-tenant", "deploy-staging", "--staging-agent", "deploy-agent", "--staging-endpoint", "scanner.local", "--scanner-version", "0.5.0", "--corpus-version", "0.1.0", "--corpus-snapshot-hash", "sha256:" + "a" * 64, "--seed", "42", "--scan-log-db", str(db), "--admin-token", "staged-admin", "--out", str(out)], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 1
    assert "scan_log_pins FAILED" in result.stderr
    assert "norow" in result.stderr
