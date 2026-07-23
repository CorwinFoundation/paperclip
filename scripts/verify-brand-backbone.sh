#!/usr/bin/env bash
# Brand Backbone gate — exits 0 if the on-disk PDF matches the pinned SHA,
# non-zero otherwise. Run before shipping any external-bound artifact.
#
# Usage:
#   scripts/verify-brand-backbone.sh
#
# Exit codes:
#   0  pinned SHA matches → safe to ship (visual/voice review still required)
#   1  brand/ directory missing or .sha256 pin file missing
#   2  PDF missing on disk — re-pull from Paperclip (BEAAA-8369 Founder attachment) and retry
#   3  SHA mismatch — on-disk PDF is stale or tampered; do NOT ship; escalate to CMO
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRAND_DIR="${ROOT}/brand"
PIN_FILE="${BRAND_DIR}/BackBond-Brand-Backbone-v1.6.sha256"
PDF_FILE="${BRAND_DIR}/BackBond-Brand-Backbone-v1.6.pdf"

if [[ ! -d "${BRAND_DIR}" || ! -f "${PIN_FILE}" ]]; then
  echo "FAIL: brand/ directory or pinned .sha256 missing at ${BRAND_DIR}" >&2
  exit 1
fi

if [[ ! -f "${PDF_FILE}" ]]; then
  echo "FAIL: ${PDF_FILE} missing - re-pull from Paperclip Founder attachment and retry" >&2
  exit 2
fi

cd "${BRAND_DIR}"
if sha256sum -c "$(basename "${PIN_FILE}")" >/dev/null 2>&1; then
  echo "OK: Brand Backbone v1.6 SHA matches pin (a2ffc4f9...)"
  exit 0
fi

ACTUAL="$(sha256sum "$(basename "${PDF_FILE}")" | awk '{print $1}')"
EXPECTED="$(awk '{print $1}' "$(basename "${PIN_FILE}")")"
echo "FAIL: SHA mismatch - on-disk PDF is NOT the pinned Backbone." >&2
echo "  expected: ${EXPECTED}" >&2
echo "  actual:   ${ACTUAL}" >&2
echo "  Do NOT ship. Re-pull the current Founder-authorized Backbone attachment and escalate to CMO." >&2
exit 3
