#!/usr/bin/env bash
# BEAAA-24725 Part C: clean-host install + rollback drill.
#
# Runs the PRODUCTION-INGEST.md "Clean-host install" and "Rollback" sections as
# written, on a host prepared by nothing else, under systemd, as root. Every
# command is echoed with its output; the drill aborts on the first failure, so a
# transcript that reaches DRILL PASSED is a transcript in which every step
# worked.
#
# Expects the candidate mounted read-only at /candidate. Substrate setup is in
# artifacts/BEAAA-24725/Dockerfile.drill; see the receipt for what that substrate
# does and does not prove.
set -euo pipefail

DOC=/candidate/ops/paperclip/numbat/PRODUCTION-INGEST.md
NUMBAT=/candidate/ops/paperclip/numbat
ARCHIVE_SHA=dbef6e73a715adda40e7697c070a96b7844398e09c7925926b8e541e5c87ef8a
UNIT=backbond-appliance-ingest.service
PY=/opt/backbond/venv/bin/python

step() { printf '\n=== %s\n' "$*"; }
show() { printf '$ %s\n' "$*"; eval "$@"; }

receipts() {   # row content, not file bytes: a clean SQLite close checkpoints
               # the WAL and changes the file hash without changing a row
  $PY - <<'PYEOF'
import hashlib, json, sqlite3
rows = sqlite3.connect("/var/lib/backbond/log-tracker.sqlite3").execute(
    "select batch_seq,batch_hash,prev_batch_hash,events_count "
    "from appliance_ingest_receipts order by batch_seq").fetchall()
print(json.dumps(rows))
print("rows_sha256:", hashlib.sha256(json.dumps(rows).encode()).hexdigest())
PYEOF
}

step "CANDIDATE UNDER DRILL"
show "git -C /candidate rev-parse HEAD"
show "git -C /candidate log -1 --format='%s'"
show "sha256sum $DOC"

step "DOC STEP 1 -- sealed artifact"
show "sha256sum $NUMBAT/backbond-appliance-0.1.0.zip"
echo "$ARCHIVE_SHA  $NUMBAT/backbond-appliance-0.1.0.zip" | sha256sum -c -
show "unzip -q $NUMBAT/backbond-appliance-0.1.0.zip -d /opt/backbond"
show "mv /opt/backbond/backbond-appliance /opt/backbond/appliance-0.1.0"
show "ls /opt/backbond/appliance-0.1.0/appliance/batch.py"

step "DOC STEP 2 -- virtualenv and the Log Tracker package"
show "python3 -m venv /opt/backbond/venv"
# staged to a writable path first: the candidate is mounted read-only here and
# setuptools builds in-tree. Not a doc step.
show "cp -r /candidate/tools/log-tracker /root/log-tracker-src"
show "$PY -m pip -q install /root/log-tracker-src"
show "$PY -c 'import log_tracker; print(log_tracker.__file__)'"

step "DOC STEP 3 -- the sealed appliance's own dependencies"
show "$PY -m pip -q install -r /opt/backbond/appliance-0.1.0/requirements.txt"
show "$PY -c 'import cryptography; print(cryptography.__version__)'"

step "DOC STEP 4 -- service identity and directories"
show "groupadd -r backbond"
show "useradd -r -g backbond -s /usr/sbin/nologin -d /var/lib/backbond backbond"
show "install -d -o backbond -g backbond -m 0750 /var/lib/backbond"
show "install -d -o root -g backbond -m 0750 /etc/backbond"
show "id backbond"

step "DOC STEP 5 -- migration"
show "install -D -m 0644 /candidate/tools/log-tracker/migrations/0002_appliance_ingest.sql /opt/backbond/migrations/0002_appliance_ingest.sql"

step "PRODUCER -- customer-side signer, present only so the drill has genuine batches"
show "python3 -m venv /root/producer-venv"
show "/root/producer-venv/bin/pip -q install -r /opt/backbond/appliance-0.1.0/requirements.txt"
show "mkdir -p /root/drill"
show "cp /candidate/artifacts/BEAAA-24725/drill_producer.py /root/drill/producer.py"
show "/root/producer-venv/bin/python /root/drill/producer.py pubkey > /root/drill/appliance.pub"

step "DOC STEP 6 -- configuration files and their ownership"
$PY - <<'PYEOF'
import json
json.dump({"appliance_id": "a1", "tenant_id": "tenantA", "source_id": "enso-synthetic",
           "public_key_pem": open("/root/drill/appliance.pub").read(),
           "token_env": "BACKBOND_INGEST_TOKEN"},
          open("/etc/backbond/appliance-enrollment.json", "w"), indent=2)
PYEOF
show "printf 'BACKBOND_INGEST_TOKEN=drill-token\n' > /etc/backbond/appliance-ingest.env"
show "chown root:backbond /etc/backbond/appliance-enrollment.json"
show "chmod 0640 /etc/backbond/appliance-enrollment.json"
show "chmod 0600 /etc/backbond/appliance-ingest.env"
show "ls -l /etc/backbond/"

step "DOC STEP 7 -- install, enable, confirm serving"
show "install -m 0644 $NUMBAT/$UNIT /etc/systemd/system/"
show "systemctl daemon-reload"
show "systemd-analyze verify /etc/systemd/system/$UNIT && echo 'unit verifies clean'"
show "systemctl enable --now $UNIT"
sleep 4
show "systemctl is-active $UNIT"
show "systemctl is-enabled $UNIT"
show "curl -sS -o /dev/null -w 'healthz http=%{http_code}\n' http://127.0.0.1:8787/healthz"
[ "$(systemctl is-active $UNIT)" = active ] || { journalctl -u $UNIT --no-pager -n 40; exit 1; }

step "DELIVERY -- a genuine batch is accepted at seq 1"
show "/root/producer-venv/bin/python /root/drill/producer.py emit b1 /root/drill/batch1.json"
show "/root/producer-venv/bin/python /root/drill/producer.py deliver /root/drill/batch1.json"
show "receipts"

step "PART A on the deployed service -- an unsigned rider is refused"
$PY - <<'PYEOF'
import json
batch = json.load(open("/root/drill/batch1.json"))
batch["evidence_tier"] = {"tier": "attested", "attested_by": "self"}
json.dump(batch, open("/root/drill/rider.json", "w"))
print("the rider shares the delivered batch_hash:", batch["batch_hash"][:27])
PYEOF
show "! /root/producer-venv/bin/python /root/drill/producer.py deliver /root/drill/rider.json"
show "receipts"

step "PART B on the deployed service -- the metrics surface and its scoping"
show "curl -sS -H 'Authorization: Bearer drill-token' -H 'X-BackBond-Tenant: tenantA' http://127.0.0.1:8787/metrics"
echo
show "curl -sS -o /dev/null -w 'cross-tenant read http=%{http_code}\n' -H 'Authorization: Bearer drill-token' -H 'X-BackBond-Tenant: tenantB' http://127.0.0.1:8787/metrics"
show "curl -sS -o /dev/null -w 'unauthenticated read http=%{http_code}\n' http://127.0.0.1:8787/metrics"

step "ROLLBACK -- stop and disable"
show "receipts > /root/drill/pre-rollback.txt; cat /root/drill/pre-rollback.txt"
show "systemctl disable --now $UNIT"
show "systemctl is-active $UNIT || true"
show "systemctl is-enabled $UNIT || true"

step "ROLLBACK -- delivery fails closed and visibly, and writes nothing"
show "/root/producer-venv/bin/python /root/drill/producer.py emit b2 /root/drill/batch2.json"
show "! /root/producer-venv/bin/python /root/drill/producer.py deliver /root/drill/batch2.json"
show "curl -sS -o /dev/null -w 'healthz while rolled back: %{http_code}\n' --max-time 5 http://127.0.0.1:8787/healthz || echo \"curl exited \$? -- refused, not a slow success\""
show "receipts > /root/drill/during-rollback.txt; cat /root/drill/during-rollback.txt"
show "diff /root/drill/pre-rollback.txt /root/drill/during-rollback.txt && echo 'receipt table unchanged across the rollback window'"

step "RESTORE -- the receipt table is reopened, not recreated"
show "systemctl enable --now $UNIT"
sleep 4
show "systemctl is-active $UNIT"
show "curl -sS -o /dev/null -w 'healthz after restore: %{http_code}\n' http://127.0.0.1:8787/healthz"
echo "-- the last pre-rollback batch must answer duplicate and write nothing --"
show "/root/producer-venv/bin/python /root/drill/producer.py deliver /root/drill/batch1.json"
echo "-- the batch built during the window must chain onto the pre-rollback tip --"
show "/root/producer-venv/bin/python /root/drill/producer.py deliver /root/drill/batch2.json"
show "receipts"
$PY - <<'PYEOF'
import sqlite3
rows = sqlite3.connect("/var/lib/backbond/log-tracker.sqlite3").execute(
    "select batch_seq,batch_hash,prev_batch_hash from appliance_ingest_receipts "
    "order by batch_seq").fetchall()
assert [r[0] for r in rows] == [1, 2], rows
assert rows[0][2] is None, rows
assert rows[1][2] == rows[0][1], "seq 2 did not chain onto the pre-rollback tip"
print("chain intact across the rollback: seq 2 prev == pre-rollback tip")
PYEOF
show "curl -sS -H 'Authorization: Bearer drill-token' -H 'X-BackBond-Tenant: tenantA' http://127.0.0.1:8787/metrics"
echo

step "JOURNALD -- one structured line per request outcome"
show "journalctl -u $UNIT --no-pager --output=cat | grep '\"event\":\"appliance_ingest\"'"
show "journalctl -u $UNIT --no-pager --output=cat | grep -c 'tenantA' || echo 'tenant id appears in no line'"

printf '\n=== DRILL PASSED\n'
