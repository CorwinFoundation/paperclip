#!/usr/bin/env python3
"""BEAAA-36046 — a terminal receiver rejection must not spin.

Before this, shipping raised inside cmd_run before the checkpoint committed, so
every 5-minute tick re-read the identical window, redid ~79s of normalize+rules
work, got the same HTTP 422 unknown_appliance, and died again. ~2,300 times.

What is asserted here:
  1. a terminal rejection parks the batch and exits with a distinct code, naming
     unknown_appliance and the appliance_id on stderr
  2. the reader checkpoint does NOT advance past an undelivered batch
  3. the next tick does NOT re-read the window (that is the flat-CPU claim)
  4. once the receiver admits, the parked batch drains and the checkpoint moves
"""
import io, json, os, sys, tempfile, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from appliance import main as M


class FakeReader:
    """Counts its own reads, so 'did the tick re-read the window' is a number."""
    reads = 0
    committed: list = []

    def __init__(self, cfg):
        self.cfg = cfg

    def discover(self):
        return True

    def configure_checkpoint(self, state_dir):
        self.state_dir = state_dir

    def read_window(self, max_events):
        FakeReader.reads += 1
        event = {"type": "tool_call", "id": f"evt-{FakeReader.reads}",
                 "tenant_id": "smb-1", "agent_id": "a1",
                 "ts": "2026-09-26T06:12:51+00:00", "tool": "shell",
                 "text": "rm -rf /tmp/x"}
        return [event], {"offset": FakeReader.reads}

    def commit(self, checkpoint):
        FakeReader.committed.append(checkpoint)


def _cfg(root):
    return {
        "appliance_id": "ariclaw-paperclip-1",
        "reader": {"name": "fake", "path": root},
        "rules": {"pack": "growth_agent.cel.yaml", "allowlist": None},
        "signing": {"key_path": os.path.join(root, "appliance.key"),
                    "key_id": "test-key-1"},
        "state_dir": os.path.join(root, "state"),
        "ship": {"mode": "http", "url": "http://127.0.0.1:8787/ingest",
                 "max_events_per_batch": 5000},
        "producer": {"collector_version": "appliance-0.1.0",
                     "evidence_profile_version": "1.0",
                     "scanner_version": "na-appliance",
                     "corpus_version": "na-appliance",
                     "rule_pack_version": "0.1", "seed": 0},
    }


def _run(cfg):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = M.cmd_run(cfg)
    return rc, err.getvalue()


def main():
    failures = []
    root = tempfile.mkdtemp(prefix="beaaa36046-")
    os.makedirs(os.path.join(root, "state"), exist_ok=True)
    cfg = _cfg(root)
    M.get_reader = lambda name: FakeReader

    rejecting = lambda batch, c: (_ for _ in ()).throw(
        M.ShipRejected(422, "unknown_appliance", batch["batch_seq"]))

    # tick 1 — the receiver refuses terminally
    M._ship = rejecting
    rc, err = _run(cfg)
    if rc != M.EXIT_REJECTED:
        failures.append(f"tick1 exit {rc}, want {M.EXIT_REJECTED}")
    if "unknown_appliance" not in err:
        failures.append("tick1 stderr does not name unknown_appliance")
    if cfg["appliance_id"] not in err:
        failures.append("tick1 stderr does not name the appliance_id")
    if "Traceback" in err:
        failures.append("tick1 emitted a traceback instead of a diagnostic")
    parked = M._dead_letter_pending(cfg)
    if len(parked) != 1:
        failures.append(f"tick1 parked {len(parked)} batches, want 1")
    if FakeReader.committed:
        failures.append(f"checkpoint advanced past an undelivered batch: {FakeReader.committed}")
    reads_after_tick1 = FakeReader.reads

    # tick 2 — still refused. The window must not be read again.
    rc, err = _run(cfg)
    if rc != M.EXIT_REJECTED:
        failures.append(f"tick2 exit {rc}, want {M.EXIT_REJECTED}")
    if FakeReader.reads != reads_after_tick1:
        failures.append(f"tick2 re-read the window ({FakeReader.reads} reads); that is the spin")
    if len(M._dead_letter_pending(cfg)) != 1:
        failures.append("tick2 lost or duplicated the parked batch")
    if FakeReader.committed:
        failures.append("tick2 advanced the checkpoint past an undelivered batch")

    # tick 3 — the receiver admits. The parked batch drains, the checkpoint moves.
    delivered = []
    M._ship = lambda batch, c: delivered.append(batch["batch_seq"]) or "http 202"
    rc, err = _run(cfg)
    if rc != M.EXIT_OK:
        failures.append(f"tick3 exit {rc}, want 0 ({err.strip()})")
    if M._dead_letter_pending(cfg):
        failures.append("tick3 left a parked batch behind after a clean delivery")
    if not delivered:
        failures.append("tick3 delivered nothing")
    if not FakeReader.committed:
        failures.append("tick3 did not advance the checkpoint after delivery")

    # the parked bytes were a real signed batch, not a stub
    if failures:
        print("BEAAA-36046 DEAD-LETTER: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("BEAAA-36046 DEAD-LETTER: PASS")
    print(f"  reads across 3 ticks: {FakeReader.reads} (1 refused window, re-read 0 times)")
    print(f"  delivered batch_seqs: {delivered}")
    print(f"  checkpoints committed: {FakeReader.committed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
