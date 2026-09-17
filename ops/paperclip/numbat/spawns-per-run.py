#!/usr/bin/env python3
"""BEAAA-33250 — spawns per run, partitioned by runtime.

The hook-overhead projection on BEAAA-33227 multiplies a per-invocation cost by
a spawn count.  Only codex runs invoke the hook: Claude Code on this host has
`hooks: null`, and the wrapper guards on CODEX_HOME and accepts only codex-*
event names, so a Claude run pays nothing.  A blended figure therefore answers
neither "what does the average run cost" nor "what does a hook-paying run cost".

This prints both rows so the multiplication is honest either way.  It does not
measure latency; p50/p90 per-invocation come from the BEAAA-33227 two-leg
method and grade against the band pre-registered there.

Read the BLENDED row as a hook-cost figure and never as an activity
distribution.  Claude runs emit no codex-shaped `item.completed` events, so
this parse sees (0 tool calls, 0 agent messages) for every one of them and they
all score the fixed floor.  Measured 2026-09-17: all 1,643 claude runs scored
exactly 3.  That is the right answer for "what does this run cost the hook" --
it costs nothing -- and the wrong answer for "how busy was this run".

    spawns-per-run.py [run-log-dir]

Read-only.  Reproduces the BEAAA-33227 published figures on the blended row,
which is how you know the parse matches the measurement it is correcting.
"""
import collections
import glob
import json
import os
import statistics
import sys

DEFAULT_DIR = (
    "/mnt/paperclipdata/paperclip-offload/run-logs"
    "/59f8876e-e729-4dda-98f9-1317c2b50492"
    "/88e68019-1cb6-4c4f-83e1-9fd340fb6821"
)
# spawns = tool_calls * 2 + agent_messages + 3  (BEAAA-33227)
PRE_POST = 2
FIXED = 3
CLASSIFY_LINES = 30


def classify_and_count(path):
    """Return (runtime, command_executions, agent_messages) for one run log.

    Run logs are newline-delimited {"ts","stream","chunk"} records; the agent's
    own event stream lives inside `chunk` as escaped JSON.  Matching on the raw
    file silently fails -- `\\"subtype\\":\\"init\\"` never equals
    `"subtype":"init"` -- so decode `chunk` before looking at anything.
    """
    runtime = None
    cmds = msgs = 0
    try:
        with open(path, errors="replace") as handle:
            for index, line in enumerate(handle):
                try:
                    chunk = json.loads(line).get("chunk", "")
                except (ValueError, AttributeError):
                    continue
                if runtime is None and index <= CLASSIFY_LINES:
                    if ("Paperclip-managed Codex home" in chunk
                            or '"thread.started"' in chunk):
                        runtime = "codex"
                    elif '"subtype":"init"' in chunk:
                        runtime = "claude"
                for event in chunk.splitlines():
                    if not event.startswith('{"type":"item.completed"'):
                        continue
                    try:
                        item = json.loads(event).get("item") or {}
                    except ValueError:
                        continue
                    if item.get("type") == "agent_message":
                        msgs += 1
                    elif item.get("type") == "command_execution":
                        cmds += 1
    except OSError:
        return None, 0, 0
    return runtime, cmds, msgs


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def row(label, spawns):
    if not spawns:
        return "  %-34s n=%-6d --" % (label, 0)
    return "  %-34s n=%-6d p50=%-6.1f p90=%-6.1f mean=%.2f" % (
        label, len(spawns), percentile(spawns, 0.5),
        percentile(spawns, 0.9), statistics.mean(spawns))


def main(argv):
    directory = argv[1] if len(argv) > 1 else DEFAULT_DIR
    files = glob.glob(os.path.join(directory, "*.ndjson"))
    if not files:
        print("no run logs under %s" % directory, file=sys.stderr)
        return 2

    by_runtime = collections.defaultdict(list)
    for path in files:
        runtime, cmds, msgs = classify_and_count(path)
        by_runtime[runtime or "unclassified"].append(cmds * PRE_POST + msgs + FIXED)
        if cmds:
            by_runtime[(runtime or "unclassified") + "+tools"].append(
                cmds * PRE_POST + msgs + FIXED)

    codex = by_runtime["codex"]
    claude = by_runtime["claude"]
    unclassified = by_runtime["unclassified"]
    blended = codex + claude + unclassified
    blended_with_tools = (by_runtime["codex+tools"] + by_runtime["claude+tools"]
                          + by_runtime["unclassified+tools"])

    print("spawns per run — %d run logs under %s" % (len(files), directory))
    print("formula: tool_calls * %d + agent_messages + %d" % (PRE_POST, FIXED))
    print()
    print("HOOK-PAYING (codex only) — the population that actually pays the hook")
    print(row("all codex runs", codex))
    print(row("codex runs with >0 tool calls", by_runtime["codex+tools"]))
    print()
    print("BLENDED (every run) — comparable to the BEAAA-33227 published figures")
    print(row("all runs", blended))
    print(row("runs with >0 tool calls", blended_with_tools))
    print()
    share = 100.0 * len(blended_with_tools) / len(blended) if blended else 0.0
    print("  claude runs (hook fires zero times): %d of %d (%.1f%%)"
          % (len(claude), len(blended), 100.0 * len(claude) / len(blended)))
    print("  runs with >0 tool calls: %d of %d (%.1f%%)"
          % (len(blended_with_tools), len(blended), share))
    if unclassified:
        print("  UNCLASSIFIED: %d — investigate before trusting these numbers"
              % len(unclassified))
    print()
    floor = PRE_POST * 0 + FIXED
    print("Claude runs emit no codex-shaped items, so each scores the floor of "
          "%d. The blended row is a hook-cost figure, not an activity one." % floor)
    print("Latency is not measured here. Multiply a row above by the "
          "per-invocation p50/p90 from the BEAAA-33227 two-leg method.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
