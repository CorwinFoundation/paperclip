#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/beaaa-15510/rerun"
EVIDENCE = OUT / "evidence"
SUMMARY = OUT / "execution_manifest.json"
EXPECTED_CASES = 90


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    summary = load_json(SUMMARY)
    evidence_paths = sorted(EVIDENCE.glob("*.json"))

    require(summary["ticket"] == "BEAAA-15510", "wrong ticket")
    require(summary["classification"] == "rule-execution", "wrong classification")
    require(summary["contract"] == "are-scanner-api-v1@v1.0", "wrong contract")
    require(summary["scanner_version"] == "0.4.1", "wrong scanner version")
    require(summary["corpus_version"] == "2026.6.24", "wrong corpus version")
    require(summary["selected"] == EXPECTED_CASES, "summary selected count mismatch")
    require(summary["attempted"] == EXPECTED_CASES, "summary attempted count mismatch")
    require(summary["evidenced"] == EXPECTED_CASES, "summary evidenced count mismatch")
    require(len(evidence_paths) == EXPECTED_CASES, "evidence file count mismatch")
    require(summary["promotion_authorized"] is False, "promotion must remain unauthorized")

    terminal = pass_count = fail_count = incomplete_count = 0
    judge_calls = parse_success_votes = retried_votes = 0
    for path in evidence_paths:
        item = load_json(path)
        require(item["contract"] == "are-scanner-api-v1@v1.0", f"{path}: wrong contract")
        require(item["scanner_version"] == "0.4.1", f"{path}: wrong scanner version")
        require(item["corpus_version"] == "2026.6.24", f"{path}: wrong corpus version")
        require(item["attempted"] is True, f"{path}: not attempted")
        require(item["verdict"] in {"pass", "fail", "incomplete"}, f"{path}: invalid verdict")
        require(isinstance(item["judge_votes"], list), f"{path}: judge votes must be a list")

        if item["terminal"]:
            terminal += 1
            require(item["verdict"] in {"pass", "fail"}, f"{path}: terminal case cannot be incomplete")
            require(len(item["judge_votes"]) == 3, f"{path}: terminal case must have 3 votes")
        else:
            incomplete_count += 1
            require(item["verdict"] == "incomplete", f"{path}: nonterminal case must be incomplete")

        if item["verdict"] == "pass":
            pass_count += 1
        elif item["verdict"] == "fail":
            fail_count += 1

        for vote in item["judge_votes"]:
            require(vote["status"] in {"complete", "incomplete"}, f"{path}: invalid vote status")
            require(vote["seed"] in {0, 1, 2}, f"{path}: invalid judge seed")
            require(vote["judge_model"] == "anthropic:claude-sonnet-4-6", f"{path}: wrong judge")
            require(vote["temperature"] == 0, f"{path}: wrong judge temperature")
            require(1 <= len(vote["attempts"]) <= 2, f"{path}: retry budget exceeded")
            for attempt in vote["attempts"]:
                require(attempt["attempt"] in {1, 2}, f"{path}: invalid attempt number")
                require(attempt["status"] in {"valid", "invalid"}, f"{path}: invalid attempt status")
                require("error_class" in attempt, f"{path}: missing attempt error class")
                require("response_sha256" in attempt, f"{path}: missing attempt response hash")
            judge_calls += len(vote["attempts"])
            retried_votes += int(len(vote["attempts"]) > 1)
            if vote["status"] == "complete":
                parse_success_votes += 1
                require(vote["verdict"] in {"PASS", "FAIL"}, f"{path}: complete vote missing verdict")
            else:
                require(vote["verdict"] is None, f"{path}: incomplete vote must not carry verdict")

    require(summary["terminal"] == terminal, "terminal count mismatch")
    require(summary["verdicts"]["pass"] == pass_count, "pass count mismatch")
    require(summary["verdicts"]["fail"] == fail_count, "fail count mismatch")
    require(summary["verdicts"]["incomplete"] == incomplete_count, "incomplete count mismatch")
    require(summary["judge_calls"] == judge_calls, "judge call count mismatch")
    require(summary["parse_success_votes"] == parse_success_votes, "parse success count mismatch")
    require(summary["retried_votes"] == retried_votes, "retry count mismatch")

    print(json.dumps({
        "status": "ok",
        "evidence_files": len(evidence_paths),
        "terminal": terminal,
        "verdicts": summary["verdicts"],
        "judge_calls": judge_calls,
        "parse_success_votes": parse_success_votes,
        "retried_votes": retried_votes,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
