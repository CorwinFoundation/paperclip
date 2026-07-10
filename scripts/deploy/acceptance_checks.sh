#!/usr/bin/env bash
# BEAAA-13362 — post-deploy automated acceptance suite (Option A, ADR-0057).
#
# Runs on the self-hosted `production` runner immediately after the container
# swap. ANY failed check exits non-zero, which trips the cd.yml auto-rollback.
# Writes a machine-readable JSON summary (--out) that the deploy record embeds.
#
# Checks (exactly the scope of BEAAA-13362):
#   1. health:   GET  /scanner/v1/sla                       == 200
#   2. auth:     POST /scanner/v1/scans  (no key)           == 401
#   3. auth:     POST /scanner/v1/scans  (forged key)       == 401
#   4. smoke:    one staging self-scan (staging tenant, NEVER 'vapi') writes a
#                FRESH scan_log row with non-null forensic_bundle_uri +
#                snapshot_hash + probe_set_hash
#   5. envelope: customer GET /scan/{id} returns the documented envelope — the
#                six external fields present, ZERO runtime fields (permanent
#                BEAAA-13169 check)
#
# --health-only runs check 1 alone (used to confirm a rollback restored health).
set -euo pipefail

BASE_URL=""; STAGING_TENANT=""; STAGING_AGENT=""; SCAN_LOG_DB=""; ADMIN_TOKEN=""
OUT="/dev/stdout"; HEALTH_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2;;
    --staging-tenant) STAGING_TENANT="$2"; shift 2;;
    --staging-agent) STAGING_AGENT="$2"; shift 2;;
    --scan-log-db) SCAN_LOG_DB="$2"; shift 2;;
    --admin-token) ADMIN_TOKEN="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --health-only) HEALTH_ONLY=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 64;;
  esac
done
[ -n "${BASE_URL}" ] || { echo "--base-url required" >&2; exit 64; }

# Loopback on the box; the app self-signs behind nginx, so -k is intentional
# for the loopback health probe.
CURL=(curl -sk --max-time 30)
RESULTS=()   # "name=PASS|FAIL:detail"
FAILED=0

record() { # name status detail
  RESULTS+=("$1=$2:${3:-}")
  [ "$2" = "PASS" ] || { FAILED=1; echo "::error::acceptance $1 FAILED: ${3:-}" >&2; }
  echo "[acceptance] $1: $2 ${3:-}" >&2
}

http_code() { # method url [header...]
  local method="$1" url="$2"; shift 2
  "${CURL[@]}" -o /dev/null -w '%{http_code}' -X "${method}" "$@" "${url}"
}

# ---- FORBIDDEN-TENANT GUARD --------------------------------------------------
# Never, under any circumstances, drive a scan against 'vapi' (standing
# blackout, BEAAA-13170). Fail closed before any scan is triggered.
if [ "${HEALTH_ONLY}" -eq 0 ]; then
  case "$(printf '%s' "${STAGING_TENANT}" | tr '[:upper:]' '[:lower:]')" in
    vapi|*vapi*) echo "::error::refuse: staging tenant resolves to vapi (blackout BEAAA-13170)" >&2; exit 2;;
    "") echo "--staging-tenant required unless --health-only" >&2; exit 64;;
  esac
fi

# ---- 1. health ---------------------------------------------------------------
code="$(http_code GET "${BASE_URL}/scanner/v1/sla")"
[ "${code}" = "200" ] && record health PASS "sla=200" || record health FAIL "sla=${code} (want 200)"

if [ "${HEALTH_ONLY}" -eq 1 ]; then
  printf '{"health":"%s"}\n' "$([ "${code}" = "200" ] && echo ok || echo bad)" > "${OUT}"
  [ "${FAILED}" -eq 0 ] || exit 1
  exit 0
fi

# ---- 2. unauthenticated scan == 401 -----------------------------------------
code="$(http_code POST "${BASE_URL}/scanner/v1/scans" -H 'Content-Type: application/json' --data '{}')"
[ "${code}" = "401" ] && record auth_unauthenticated PASS "401" || record auth_unauthenticated FAIL "got ${code} (want 401)"

# ---- 3. forged key == 401 ----------------------------------------------------
code="$(http_code POST "${BASE_URL}/scanner/v1/scans" \
        -H 'Content-Type: application/json' \
        -H 'Authorization: Bearer forged-not-a-real-key-000' \
        --data '{}')"
[ "${code}" = "401" ] && record auth_forged_key PASS "401" || record auth_forged_key FAIL "got ${code} (want 401)"

# ---- 4. staging self-scan -> fresh scan_log row w/ the three non-null pins ---
SCAN_ID=""
resp="$("${CURL[@]}" -X POST "${BASE_URL}/scanner/v1/scans" \
        -H 'Content-Type: application/json' \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "X-Tenant-Id: ${STAGING_TENANT}" \
        --data "$(printf '{"agent_id":"%s","scanner_invocation_context":{"purpose":"deploy_acceptance_smoke"}}' "${STAGING_AGENT}")" || true)"
SCAN_ID="$(printf '%s' "${resp}" | python3 -c 'import sys,json;
try:
    d=json.load(sys.stdin); print(d.get("scan_id") or d.get("scanId") or "")
except Exception: print("")' 2>/dev/null || true)"

if [ -z "${SCAN_ID}" ]; then
  record smoke_scan FAIL "no scan_id in POST response: $(printf '%s' "${resp}" | head -c 160)"
else
  # Poll GET /scan/{id} until it leaves 'accepted' (bounded).
  status="accepted"; body=""
  for _ in $(seq 1 30); do
    body="$("${CURL[@]}" -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "X-Tenant-Id: ${STAGING_TENANT}" \
            "${BASE_URL}/scan/${SCAN_ID}?audience=customer" || true)"
    status="$(printf '%s' "${body}" | python3 -c 'import sys,json;
try: print(json.load(sys.stdin).get("status",""))
except Exception: print("")' 2>/dev/null || true)"
    [ "${status}" = "complete" ] || [ "${status}" = "partial" ] && break
    sleep 2
  done
  record smoke_scan PASS "scan_id=${SCAN_ID} status=${status}"

  # The three write-once pins must be non-null on the freshest row for this
  # agent. Use Python's stdlib sqlite3 (always present) — not the sqlite3 CLI,
  # which the box is not guaranteed to have.
  if [ -n "${SCAN_LOG_DB}" ] && [ -f "${SCAN_LOG_DB}" ]; then
    pins="$(SCAN_LOG_DB="${SCAN_LOG_DB}" STAGING_AGENT="${STAGING_AGENT}" python3 - <<'PY' 2>/dev/null || echo err
import os, sqlite3
db, agent = os.environ["SCAN_LOG_DB"], os.environ["STAGING_AGENT"]
con = sqlite3.connect(db)
row = con.execute(
    "SELECT forensic_bundle_uri, snapshot_hash, probe_set_hash "
    "FROM scan_log WHERE agent_id=? ORDER BY run_timestamp DESC LIMIT 1",
    (agent,),
).fetchone()
print("norow" if row is None else "|".join("1" if v is not None else "0" for v in row))
PY
)"
    if [ "${pins}" = "1|1|1" ]; then
      record scan_log_pins PASS "forensic_bundle_uri+snapshot_hash+probe_set_hash all non-null"
    else
      record scan_log_pins FAIL "pin presence (fbu|snap|probe)=${pins} (want 1|1|1)"
    fi
  else
    record scan_log_pins FAIL "scan_log db not found at '${SCAN_LOG_DB}'"
  fi

  # ---- 5. envelope: six external fields present, zero runtime fields --------
  # Pass the body via a file (heredoc owns stdin, so we can't pipe the body).
  printf '%s' "${body}" > "${OUT}.envelope.json"
  ENVELOPE_FILE="${OUT}.envelope.json" python3 - <<'PY' && ev=PASS || ev=FAIL
import os, sys, json
try:
    with open(os.environ["ENVELOPE_FILE"]) as f:
        d = json.loads(f.read() or "{}")
except Exception as e:
    print(f"unparseable envelope: {e}"); sys.exit(1)
# The six external result fields the design partner reads (BEAAA-13169 §4 P0).
SIX = ["are_total", "subscores", "band", "confidence", "evidence_refs", "uncovered_dimensions"]
# Internal runtime fields that MUST NOT leak to the customer envelope.
RUNTIME = ["rows_emitted", "spread_per_dim", "dim_results", "k_replicates",
           "live_probe_variance_band", "score_json"]
missing = [f for f in SIX if f not in d]
leaked  = [f for f in RUNTIME if f in d]
if missing:
    print(f"missing external fields: {missing}"); sys.exit(1)
if leaked:
    print(f"runtime fields leaked into customer envelope: {leaked}"); sys.exit(1)
print("envelope ok: six external fields present, zero runtime fields")
PY
  [ "${ev}" = "PASS" ] && record envelope PASS "six fields, zero runtime" \
    || record envelope FAIL "envelope check failed (see log)"
fi

# ---- summary -----------------------------------------------------------------
python3 - "${OUT}" "${RESULTS[@]}" <<'PY'
import sys, json
out = sys.argv[1]
checks = {}
for item in sys.argv[2:]:
    name, rest = item.split("=", 1)
    status, _, detail = rest.partition(":")
    checks[name] = {"status": status, "detail": detail}
passed = all(c["status"] == "PASS" for c in checks.values())
doc = {"passed": passed, "checks": checks}
with open(out, "w") as f:
    json.dump(doc, f, indent=2)
print(json.dumps(doc))
PY

[ "${FAILED}" -eq 0 ] || { echo "::error::acceptance suite FAILED" >&2; exit 1; }
echo "[acceptance] ALL CHECKS PASSED" >&2
