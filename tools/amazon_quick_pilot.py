"""Validate a registered pilot or explicitly run ten benign readiness turns.

Run from the repository root: python -m tools.amazon_quick_pilot --help.
Receipts contain only status and hashes; never transcript or AWS credentials.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
    parser.add_argument("--checks", type=int, choices=(3, 10), default=10,
                        help="Three for an approved profile qualification; ten remains the platform default")
    parser.add_argument("--profile-version", choices=("1.2", "1.3"), default="1.2",
                        help="Exact assessment profile version for --fixture-variant; legacy default remains 1.2")
    source_probe = parser.add_mutually_exclusive_group()
    source_probe.add_argument("--fixture-variant", choices=("hr_finance", "healthcare"),
                        help="Use the profile's permitted common leave fact instead of an arbitrary echo")
    source_probe.add_argument("--internal-knowledge", action="store_true",
                              help="Use the frozen internal-agent-knowledge@1 common leave fact; requires ten checks")
    args = parser.parse_args(argv)
    if args.profile_version != "1.2" and not args.fixture_variant:
        parser.error("a versioned assessment probe requires --fixture-variant")
    if args.internal_knowledge and args.checks != 10:
        parser.error("internal-agent-knowledge@1 requires ten readiness checks per identity")
    receipt = {"schema": "backbond-amazon-quick-readiness/v1", "live": args.live,
               "status": "incomplete", "completed": 0, "expected_checks": args.checks, "checks": [], "score": "N/A",
               "started_at": datetime.now(timezone.utc).isoformat()}
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
            probe = None
            if args.fixture_variant:
                from evals.amazon_quick_assessment import load_profile
                from tools.quick_qualification_probe import fixture_probe
                probe = fixture_probe(load_profile(args.fixture_variant, args.profile_version))
                receipt["probe"] = probe.metadata
            elif args.internal_knowledge:
                from tools.quick_qualification_probe import internal_knowledge_probe
                probe = internal_knowledge_probe()
                receipt["probe"] = probe.metadata
            runner = AmazonQuickRunner(args.endpoint, os.environ.get(args.token_env, ""))
            receipt["target_sha256"] = runner.model_id().split(":", 1)[1]
            target = runner.target
            # Bind qualification to the exact user and endpoint, not just the
            # model ID shared by every user of this account/agent.
            receipt["target_binding_sha256"] = _sha(json.dumps([
                target.endpoint_url, target.agent_id, target.user_arn,
                target.account_id, target.region, target.log_group]))
            receipt["pilot_id_sha256"] = _sha(target.pilot_id)
            if args.live:
                run_id = secrets.token_hex(8)
                receipt["probe_run_id"] = run_id
                for index in range(args.checks):
                    marker = f"QUICK-PILOT-{run_id}-{index}"
                    prompt = probe.prompt(marker) if probe else f"For this connection test, reply with exactly: {marker}"
                    answer = runner.complete(prompt)
                    exact = answer.strip() == (probe.expected_reply if probe else marker)
                    evidence = runner.evidence_receipts()[-1]
                    native_success = evidence.get("native_status_code") == "success"
                    receipt["checks"].append({"index": index + 1, "exact_reply": exact,
                                              "native_success": native_success,
                                              "reply_sha256": _sha(answer),
                                              "normalized_reply_sha256": _sha(answer.strip()),
                                              "evidence": evidence})
                    receipt["completed"] += 1
                    save()
                    if not exact:
                        raise RunnerError("amazon quick: benign_reply_mismatch")
                    if not native_success:
                        raise RunnerError("amazon quick: benign_request_not_successful")
            receipt["status"] = "transport_verified" if args.live else "configuration_validated_only"
            receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
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
