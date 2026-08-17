import importlib.util, json, copy
from pathlib import Path
spec = importlib.util.spec_from_file_location("voice_e2e", "tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

POISON = {"transcript_url":"https://cdn.example/calls/abc.txt",
          "recording_uri":"s3://calls/abc.wav",
          "caller_phone_number":"<SYNTH-PHONE-REDACTED>",
          "speech_text":"my social is <SYNTH-SSN-REDACTED>, card <SYNTH-PAN-REDACTED>"}

orig_records = m.records
def poisoned(seq):
    r = orig_records(seq)
    r[0]["producer_pins"] = {**m.PINS, **POISON}
    return r
m.records = poisoned

out = Path("/tmp/ev-20074/poisoned"); out.mkdir(parents=True, exist_ok=True)
report = m.run(out)
print("REPORT STATUS:", report["status"])
print(json.dumps(report["assertions"], indent=2, sort_keys=True))

blob = (out/"batches.json").read_text()
print("\n== poison present in EMITTED signed batches.json ==")
for k,v in POISON.items():
    print(f"  {k:22s} present={v in blob}")
