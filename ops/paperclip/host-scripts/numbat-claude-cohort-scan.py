#!/usr/bin/env python3
"""BEAAA-24636 — runtime-agnostic Numbat capture for the BEAAA-21248 cohort.

Captures Claude-runtime sessions for exactly three authorized seats by scanning
their on-disk transcripts with the pinned numbat binary and appending the emitted
records to each seat's existing records/<seat>/records.ndjson.

Why this shape:
  * The Numbat lifecycle hook instruments codex only, so claude_local seats went dark.
  * ~/.claude/settings.json is host-wide across 14 claude_local seats, so a hook
    install there would over-collect 11 seats outside the cohort. Not done.
  * Claude project directories are keyed by cwd, not by seat, and the cohort's
    sessions land mostly in a SHARED project directory alongside non-cohort seats.
    So directory scoping does not hold either. Attribution is per session file.

Scope guard: a transcript is captured only when its own first
"You are agent <uuid>" marker is an exact match for one of the three COHORT ids.
No marker, or any other id, means skip. A fourth seat cannot be captured because
there is no path from a non-matching uuid to an output file.
"""
import json, os, re, subprocess, sys, tempfile, time

NUMBAT = "/usr/local/bin/numbat"
RECORDS_ROOT = "/mnt/paperclipdata/numbat/records"
PROJECTS = os.path.expanduser("~/.claude/projects")

# BEAAA-21248 cohort. Exact match only.
COHORT = {
    "88e68019-1cb6-4c4f-83e1-9fd340fb6821": "cto",
    "bc8396d5-dee6-4ad8-ae3f-1a2280088cb4": "scanner-engineer-2",
    "0c4b497c-ff5d-4a0e-abf4-70da82fed600": "auditor",
}

MARKER = re.compile(rb"You are agent ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
SETTLE_SECONDS = 600   # only scan a transcript once its session has gone quiet
MAX_AGE_DAYS = 8       # skip files older than the rotation window (7d retention + 1d buffer)
STATE_NAME = ".claude-scan-state.json"


def owner_of(path):
    """First self-identification wins. Later matches are the seat quoting another
    seat's prompt, which is why this is first-match and not any-match."""
    try:
        with open(path, "rb") as f:
            for line in f:
                m = MARKER.search(line)
                if m:
                    return m.group(1).decode()
    except OSError:
        return None
    return None


def load_state(seat):
    p = os.path.join(RECORDS_ROOT, seat, STATE_NAME)
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(seat, state):
    d = os.path.join(RECORDS_ROOT, seat)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, STATE_NAME)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def scan_events(path):
    """Run the pinned numbat scanner over one transcript; return its event records
    in file order. Diagnostics and scan_summary are dropped."""
    with tempfile.NamedTemporaryFile(suffix=".ndjson", delete=False) as tf:
        out = tf.name
    try:
        subprocess.run(
            [NUMBAT, "scan", "--path", path, "--emit", "all",
             "--output", "file", "--output-file", out],
            check=True, capture_output=True, timeout=120,
        )
        events = []
        with open(out) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("record_type") == "event":
                    events.append(r)
        return events
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def main():
    dry = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or dry
    now = time.time()

    considered = skipped_non_cohort = skipped_unmarked = 0
    per_seat = {s: {"files": 0, "new_events": 0} for s in COHORT.values()}
    states = {s: load_state(s) for s in COHORT.values()}

    if not os.path.isdir(PROJECTS):
        print(f"no claude projects dir at {PROJECTS}", file=sys.stderr)
        return 1

    for proj in sorted(os.listdir(PROJECTS)):
        pdir = os.path.join(PROJECTS, proj)
        if not os.path.isdir(pdir):
            continue
        for name in sorted(os.listdir(pdir)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(pdir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if now - st.st_mtime > MAX_AGE_DAYS * 86400:
                continue
            considered += 1
            owner = owner_of(path)
            if owner is None:
                skipped_unmarked += 1
                continue
            seat = COHORT.get(owner)
            if seat is None:
                skipped_non_cohort += 1
                continue
            if now - st.st_mtime < SETTLE_SECONDS:
                if verbose:
                    print(f"  live, deferring: {seat} {name}")
                continue
            state = states[seat]
            prev = state.get(path)
            if prev and prev.get("size") == st.st_size:
                continue

            already = prev.get("events", 0) if prev else 0
            events = scan_events(path)
            fresh = events[already:]
            per_seat[seat]["files"] += 1
            per_seat[seat]["new_events"] += len(fresh)
            if verbose:
                print(f"  {seat}: {name} -> {len(fresh)} new event(s) (had {already})")
            if not dry and fresh:
                d = os.path.join(RECORDS_ROOT, seat)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "records.ndjson"), "a") as f:
                    for r in fresh:
                        f.write(json.dumps(r, separators=(",", ":")) + "\n")
            if not dry:
                state[path] = {"size": st.st_size, "events": len(events),
                               "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    if not dry:
        for seat, state in states.items():
            save_state(seat, state)

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tally = " ".join(f"{s}={v['files']}f/{v['new_events']}e" for s, v in sorted(per_seat.items()))
    print(f"{stamp} beaaa-24636 claude-cohort-scan{' DRY-RUN' if dry else ''}: "
          f"considered={considered} non_cohort_skipped={skipped_non_cohort} "
          f"unmarked_skipped={skipped_unmarked} {tally}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
