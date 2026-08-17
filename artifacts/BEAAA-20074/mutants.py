import importlib.util, json
from pathlib import Path
SRC="tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py"
def load(tag):
    spec=importlib.util.spec_from_file_location("v_"+tag, SRC)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def go(tag, patch):
    m=load(tag); patch(m)
    out=Path(f"/tmp/ev-20074/mut_{tag}"); out.mkdir(parents=True,exist_ok=True)
    try:
        r=m.run(out); return r["status"], r["assertions"]
    except Exception as e:
        return f"EXC:{type(e).__name__}: {e}", {}

# M2: no-content scanner neutered entirely
s,a=go("M2", lambda m: setattr(m,"inspect_no_content", lambda *x,**k: None))
print("M2 inspect_no_content -> no-op        :", s, "| no_content_recursive=", a.get("no_content_recursive"))

# M3: Log Tracker leg — emit ZERO voice events into the tracker path is already the case.
#     Mutant: make normalize produce nothing at all; does log_tracker_interface still pass?
def m3(m):
    real=m.normalize_receiver_events
    m.normalize_receiver_events=lambda *x,**k: (real(*x,**k) and [])
s,a=go("M3", m3)
print("M3 zero normalized events            :", s, "| log_tracker_interface=", a.get("log_tracker_interface"),
      "| continuity=", a.get("continuity"))

# M4 (control): break the hash chain -> must be caught
def m4(m):
    real=m.signed_batch
    def f(seq,ev,prev,key): return real(seq,ev,None,key)
    m.signed_batch=f
s,a=go("M4", m4)
print("M4 chain broken (control)            :", s, "| prev_batch_hash_chain=", a.get("prev_batch_hash_chain"))
