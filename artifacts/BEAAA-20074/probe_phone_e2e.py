import importlib.util, json, sqlite3
from pathlib import Path
spec = importlib.util.spec_from_file_location("v", "tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

PHONE = "555-010-9999"          # caller phone number, one of the 4 originally-proven poisons
E164  = "14155550123"

orig = m.records
def poisoned(seq, _o=orig):
    r = _o(seq)
    r[0]["session_id"]  = PHONE     # caller phone rides in an allowed closed scalar
    r[0]["agent_id"]    = E164
    return r
m.records = poisoned

out = Path("/tmp/ev-20074/phone"); out.mkdir(parents=True, exist_ok=True)
report = m.run(out)
print("REPORT STATUS:", report["status"])
print("no_content_recursive :", report["assertions"]["no_content_recursive"])
print("nodes_scanned/violations:", report["counts"]["no_content_nodes_scanned"], "/", report["counts"]["no_content_violations"])

blob = (out/"batches.json").read_text()
print("\n== in EMITTED SIGNED batches.json ==")
print("  caller phone  present =", PHONE in blob)
print("  e164 number   present =", E164 in blob)

db = sqlite3.connect(out/"log-tracker.sqlite3")
rows = db.execute("select name from sqlite_master where type='table'").fetchall()
hit = []
for (t,) in rows:
    try:
        for r in db.execute(f"select * from {t}"):
            s = " ".join(str(x) for x in r)
            if PHONE in s or E164 in s: hit.append(t)
    except Exception: pass
print("\n== in PERSISTED BackBond-side Log Tracker rows ==")
print("  tables containing the phone/e164:", sorted(set(hit)) or "NONE")
db.close()
