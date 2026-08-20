#!/usr/bin/env python3
"""QA BEAAA-20074 R4 — Python harness mutation battery against candidate 89932217.

Each mutant neuters one thing the harness claims to measure. A mutant that
SURVIVES means the corresponding assertion is not load-bearing.
"""
import importlib.util
import shutil
from pathlib import Path

SRC = "tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py"
EVROOT = Path("/tmp/ev-20074-r4")

# The harness persists a ManifestIdempotencyStore under its output dir. Reusing a
# previous run's dir makes every mutant die as a replay instead of on its own merit,
# so each invocation starts from a clean root.
shutil.rmtree(EVROOT, ignore_errors=True)


def load(tag):
    spec = importlib.util.spec_from_file_location("v_" + tag, SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def go(tag, patch):
    m = load(tag)
    patch(m)
    out = EVROOT / f"mut_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    try:
        r = m.run(out)
        return r["status"], r["assertions"]
    except Exception as e:
        return f"EXC:{type(e).__name__}", {}


def report(label, status, assertions, watch):
    killed = status != "PASS"
    verdict = "KILLED" if killed else "*** SURVIVED ***"
    seen = assertions.get(watch, "n/a")
    print(f"{label:<40} {verdict:<16} status={status:<22} {watch}={seen}")


# control: unmutated
s, a = go("baseline", lambda m: None)
print(f"{'baseline (no mutation)':<40} {'PASS expected':<16} status={s}")
print()

# M1: recursive no-content scanner neutered
s, a = go("M1", lambda m: setattr(m, "inspect_no_content", lambda *x, **k: None))
report("M1 inspect_no_content -> no-op", s, a, "no_content_recursive")

# M1b: scanner returns a clean measurement without looking (subtler than no-op)
s, a = go("M1b", lambda m: setattr(m, "inspect_no_content",
                                   lambda *x, **k: {"nodes": 331, "violations": 0}))
report("M1b scanner -> fake clean count", s, a, "no_content_recursive")

# M4 (control): break the prev_batch_hash chain
def m4(m):
    real = m.signed_batch
    m.signed_batch = lambda seq, ev, prev, key: real(seq, ev, None, key)
s, a = go("M4", m4)
report("M4 hash chain broken (control)", s, a, "prev_batch_hash_chain")

# M5: Log Tracker persists nothing
def m5(m):
    real = m.persist_determinations
    m.persist_determinations = lambda ev, path: (real(ev, path) and [])
s, a = go("M5", m5)
report("M5 log tracker zero rows", s, a, "log_tracker_determinations_persisted")

# M6: continuity starved
def m6(m):
    real = m.compute_gap_state
    m.compute_gap_state = lambda *x, **k: None
s, a = go("M6", m6)
report("M6 continuity gate -> None", s, a, "continuity_gate_healthy")

# M10: tamper detection neutered — does tamper_rejected still claim True?
def m10(m):
    m.compute_batch_sha256 = lambda *x, **k: "sha256:" + "0" * 64
s, a = go("M10", m10)
report("M10 batch hash -> constant", s, a, "tamper_rejected")
