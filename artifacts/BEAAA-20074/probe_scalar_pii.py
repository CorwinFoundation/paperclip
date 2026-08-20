import importlib.util, json, copy
from pathlib import Path
spec = importlib.util.spec_from_file_location("v", "tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Values that satisfy ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ (no spaces, no slashes)
CASES = {
  "bare_phone":        "555-010-9999",
  "e164_phone":        "14155550123",
  "ssn_dashed":        "123-45-6789",
  "pan_dashed":        "4111-1111-1111-1111",
  "email_ish":         "caller.jane.doe-at-example.com",
  "transcript_hostpath":"cdn.example.com:transcript.abc123",
}
for name, val in CASES.items():
    orig = m.records
    def poisoned(seq, _v=val, _o=orig):
        r = _o(seq); r[0]["session_id"] = _v; return r
    m.records = poisoned
    try:
        rec = m.records(1)
        try:
            prod = m.collector_events(rec)
            crossed_go = True; err=""
        except ValueError as e:
            crossed_go = False; err=str(e)[:90]
        print(f"{name:22s} value={val:34s} crossed_Go_boundary={crossed_go}  {err}")
    finally:
        m.records = orig
