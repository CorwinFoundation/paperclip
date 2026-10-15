"""BEAAA-41114 retention preflight: verify every attempt row keeps full evidence.

    python3 tools/beaaa41114_retention_check.py ROWS.jsonl [ROWS.jsonl ...] [--out report.json]

Per attempt it checks the complete target request, the full reply (and variant-B
reply for pair checks), tool calls, and for every judged check two judge records
whose prompt contains the full reply and whose raw output is retained. Excerpt
fields (``*_excerpt``) fail the check. Exit 1 if any attempt fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

JUDGED = {"judge", "must_include", "pair"}


def check_action_row(row: dict) -> list[str]:
    """Full Scan E/U (sandbox envelope) and H (OpenRouter tool loop) attempts."""
    problems = [f"excerpt_field:{k}" for k in row if k.endswith("_excerpt")]
    if not isinstance(row.get("tool_calls"), list):
        problems.append("missing_tool_calls")
    if row["runner"] == "full_scan_e":
        turns = (row.get("transcript") or {}).get("turns")
        if not turns:
            problems.append("missing_full_transcript")
        for i, turn in enumerate(turns or []):
            if not isinstance(turn.get("user_message"), str) or "assistant_text" not in turn:
                problems.append(f"turn_incomplete:{i}")
        if row.get("verdict") != "could_not_run" and not isinstance(row.get("tool_call_log"), list):
            problems.append("missing_tool_call_log")
        for key in ("before_state", "observed_state"):
            if key not in row:
                problems.append(f"missing_{key}")
    else:
        messages = row.get("messages") or []
        if not messages or not row.get("raw_responses"):
            problems.append("missing_full_messages")
        assistant = [m for m in messages if m.get("role") == "assistant"]
        if not assistant or (assistant[-1].get("content") or "") != (row.get("reply") or ""):
            if not (row.get("reply") or "") == ((row.get("end_state") or {}).get("final_response") or "") == "":
                problems.append("reply_differs_from_last_assistant_message")
        if (row.get("end_state") or {}).get("final_response") != row.get("reply"):
            problems.append("final_response_truncated")
    return problems


def check_u_row(row: dict) -> list[str]:
    """Full Scan U live driver: generated prompts/tool schema, messages, raw responses, observation, grade."""
    problems = [f"excerpt_field:{k}" for k in row if k.endswith("_excerpt")]
    for key in ("generated_system_prompt", "generated_user_turns", "tool_schema", "messages"):
        if not row.get(key):
            problems.append(f"missing_{key}")
    if not isinstance(row.get("tool_calls"), list):
        problems.append("missing_tool_calls")
    if row.get("verdict") == "could_not_run":
        if not row.get("error") and not row.get("reason"):
            problems.append("could_not_run_without_reason")
        return problems
    if not row.get("raw_responses"):
        problems.append("missing_raw_responses")
    for key in ("observation", "grade"):
        if not row.get(key):
            problems.append(f"missing_{key}")
    assistant = "\n".join(m.get("content") or "" for m in row.get("messages") or []
                           if m.get("role") == "assistant" and not m.get("tool_calls"))
    if (row.get("reply") or "") != assistant:
        problems.append("reply_differs_from_assistant_messages")
    logged = [r for r in (row.get("observation") or {}).get("action_log", []) if r.get("tool")]
    if len(logged) != len(row.get("tool_calls") or []):
        problems.append("action_log_tool_call_count_mismatch")
    return problems


def check_row(row: dict) -> list[str]:
    if row.get("runner") == "full_scan_u":
        return check_u_row(row)
    if row.get("runner") in {"full_scan_e", "full_scan_h"}:
        return check_action_row(row)
    problems = [f"excerpt_field:{k}" for k in row if k.endswith("_excerpt")]
    if not row.get("target_request"):
        problems.append("missing_target_request")
    if "tool_calls" not in row or not isinstance(row["tool_calls"], list):
        problems.append("missing_tool_calls")
    if row.get("verdict") == "could_not_run" and not row.get("judges"):
        if row.get("reply") is None and not row.get("error"):
            problems.append("could_not_run_without_error_or_reply")
        return problems
    reply = row.get("reply")
    if not isinstance(reply, str) or not reply:
        problems.append("missing_full_reply")
    if row.get("check") == "pair" and not isinstance(row.get("reply_b"), str):
        problems.append("missing_variant_b_reply")
    if row.get("check") in JUDGED:
        judges = row.get("judges") or []
        if len({j.get("model") for j in judges}) < 2:
            problems.append("fewer_than_two_judges")
        for j in judges:
            prompt = j.get("prompt") or ""
            if not reply or (reply not in prompt and json.dumps(reply, ensure_ascii=False)[1:-1] not in prompt):
                problems.append(f"judge_prompt_lacks_full_reply:{j.get('model')}")
            if j.get("raw_output") is None and not j.get("error"):
                problems.append(f"judge_output_missing:{j.get('model')}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="+")
    ap.add_argument("--out")
    args = ap.parse_args()
    report = {"files": [], "attempts": 0, "attempts_ok": 0, "replies_over_300_chars": 0,
              "judge_records": 0, "failures": []}
    for name in args.rows:
        path = Path(name)
        data = path.read_bytes()
        report["files"].append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
        for line in data.decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            report["attempts"] += 1
            report["judge_records"] += len(row.get("judges") or [])
            reply = row.get("reply")
            if reply is None and row.get("runner") == "full_scan_e":
                reply = "\n".join(t.get("assistant_text") or "" for t in (row.get("transcript") or {}).get("turns", []))
            report["replies_over_300_chars"] += len(reply or "") > 300
            problems = check_row(row)
            if problems:
                report["failures"].append({"case_id": row.get("case_id") or row.get("stable_id"), "seed": row.get("seed"),
                                           "problems": problems})
            else:
                report["attempts_ok"] += 1
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    print(text, end="")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
