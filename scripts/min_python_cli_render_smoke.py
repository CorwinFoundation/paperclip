#!/usr/bin/env python3
"""Python 3.11 CLI-to-render smoke gate for bin/are-scan."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from report_card.render import render  # noqa: E402
from rubric.scoring import Rubric  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "synthetic_eval_outputs.json"
RUBRIC = REPO / "rubric" / "rubric.yaml"


def _write_cli_input(input_dir: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.pop("_comment", None)
    (input_dir / "eval_results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (input_dir / "model_versions.json").write_text(
        json.dumps(
            {
                "primary": "fixture-model@local",
                "judges": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run_smoke(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="are-scan-input-") as tmp:
        input_dir = Path(tmp)
        _write_cli_input(input_dir)
        scan_json = output_dir / "min-python-cli-scan.json"
        rendered_html = output_dir / "min-python-report-card.html"

        cmd = [
            sys.executable,
            str(REPO / "bin" / "are-scan"),
            "--agent",
            "beaaa16783-min-python",
            "--seed",
            "16783",
            "--corpus",
            "0.0.0",
            "--input-dir",
            str(input_dir),
            "--out",
            str(scan_json),
            "--scored-at",
            "2026-07-22T00:00:00+00:00",
        ]
        completed = subprocess.run(
            cmd,
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            sys.stderr.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            return completed.returncode

        scan_payload = json.loads(scan_json.read_text(encoding="utf-8"))
        html = render(scan_payload, Rubric.load(RUBRIC), provenance="measured")
        rendered_html.write_text(html, encoding="utf-8")
        if not rendered_html.exists() or rendered_html.stat().st_size <= 0:
            sys.stderr.write(f"rendered output missing or empty: {rendered_html}\n")
            return 1
        print(f"scan_json={scan_json}")
        print(f"rendered_html={rendered_html}")
        print(f"rendered_bytes={rendered_html.stat().st_size}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO / "artifacts" / "beaaa-16783" / "min-python-smoke",
    )
    args = parser.parse_args()
    return run_smoke(args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
