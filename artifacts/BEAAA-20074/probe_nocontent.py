import importlib.util, json, sys, copy
from pathlib import Path
spec = importlib.util.spec_from_file_location("voice_e2e", "tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Poisoned record: prohibited-EQUIVALENT content nested inside an ALLOWED key.
rec = m.records(1)
rec[0]["producer_pins"] = dict(m.PINS)
rec[0]["producer_pins"]["transcript_url"] = "https://cdn.example/calls/abc.txt"
rec[0]["producer_pins"]["recording_uri"] = "s3://calls/abc.wav"
rec[0]["producer_pins"]["caller_phone_number"] = "<SYNTH-PHONE-REDACTED>"
rec[0]["producer_pins"]["speech_text"] = "Hi, my social is <SYNTH-SSN-REDACTED>, card <SYNTH-PAN-REDACTED>"

print("== Go collector boundary ==")
try:
    prod = m.collector_events(rec)
    print("ACCEPTED by SyntheticVoiceDriver/Collector")
except ValueError as e:
    print("REJECTED:", e); sys.exit(0)

print("== emitted collector events ==")
print(json.dumps(prod["events"], indent=2, sort_keys=True))

print("== shipped recursive no-content scanner (inspect_no_content) ==")
try:
    m.inspect_no_content(prod)
    print("PASS -> scanner found NO prohibited content")
except AssertionError as e:
    print("CAUGHT:", e)
