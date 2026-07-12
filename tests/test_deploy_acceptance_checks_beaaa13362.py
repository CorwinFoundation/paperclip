import os
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
