"""Validate a registered pilot or explicitly run ten benign readiness turns.

Run from the repository root: python -m tools.amazon_quick_pilot --help.
Receipts contain only status and hashes; never transcript or AWS credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets

from harness.amazon_quick import AmazonQuickRunner, _sha
from harness.runner import RunnerError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--token-env", default="AMAZON_QUICK_PILOT_TOKEN")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--live", action="store_true", help="Submit ten benign prompts; consumes pilot call budget")
    args = parser.parse_args(argv)
    receipt = {"schema": "backbond-amazon-quick-readiness/v1", "live": args.live,
               "status": "incomplete", "completed": 0, "checks": [], "score": "N/A"}
    runner = None
    # Reserve the output before performing any live work. Never overwrite.
    try:
        fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        print(json.dumps({"status": "failed", "error": "new_receipt_path_required"}))
        return 2
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        def save():
            output.seek(0)
            json.dump(receipt, output, indent=2)
            output.truncate()
            output.flush()
            os.fsync(output.fileno())
        save()
        try:
            runner = AmazonQuickRunner(args.endpoint, os.environ.get(args.token_env, ""))
            receipt["target_sha256"] = runner.model_id().split(":", 1)[1]
            if args.live:
                run_id = secrets.token_hex(8)
                for index in range(10):
                    marker = f"QUICK-PILOT-{run_id}-{index}"
                    answer = runner.complete(f"For this connection test, reply with exactly: {marker}")
                    exact = answer.strip() == marker
                    receipt["checks"].append({"index": index + 1, "exact_reply": exact,
                                              "reply_sha256": _sha(answer),
                                              "evidence": runner.evidence_receipts()[-1]})
                    receipt["completed"] += 1
                    save()
                    if not exact:
                        raise RunnerError("amazon quick: benign_reply_mismatch")
            receipt["status"] = "transport_verified" if args.live else "configuration_validated_only"
            save()
            print(json.dumps({"status": receipt["status"], "completed": receipt["completed"], "score": "N/A"}))
            return 0
        except RunnerError as exc:
            receipt["error"] = str(exc)
        except Exception:
            receipt["error"] = "pilot_runtime_failed"
        finally:
            if runner is not None:
                runner.close()
        save()
        print(json.dumps({"status": "incomplete", "completed": receipt["completed"], "error": receipt["error"]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
