#!/bin/sh
# QA BEAAA-20074 R4 — Go source mutation battery against candidate 89932217.
# Each mutant neuters one guard, then the candidate's OWN tests are the oracle.
set -u
cd "$(dirname "$0")/.." || exit 1
SRC=tools/are-collector/internal/source/synthetic_voice.go
BAK=/tmp/qa20074-r4/qa-probes/synthetic_voice.go.orig
cp "$SRC" "$BAK"

restore() { cp "$BAK" "$SRC"; }
trap restore EXIT

run_oracle() {
  # $1 = mutant label, $2 = test name expected to catch it
  out=$(cd tools/are-collector && go test ./internal/source/ ./internal/collector/ -run 'Voice|Prohibited' 2>&1)
  if printf '%s' "$out" | grep -q "FAIL"; then
    verdict="KILLED"
  else
    verdict="*** SURVIVED ***"
  fi
  printf '%-28s %-14s expected-catcher: %s\n' "$1" "$verdict" "$2"
  printf '%s\n' "$out" | grep -E '^\s+--- FAIL|^--- FAIL|rejected for non-content' | sed 's/^/      /' | head -4
  restore
}

echo "=== baseline (unmutated) ==="
(cd tools/are-collector && go test ./internal/source/ ./internal/collector/ 2>&1 | tail -3)
echo

# M2: prohibitedPath always reports clean
python3 - "$SRC" <<'PY'
import sys,re
p=sys.argv[1]; s=open(p).read()
s=s.replace('func prohibitedPath(value any, prefix string) (string, bool) {',
            'func prohibitedPath(value any, prefix string) (string, bool) {\n\treturn "", false // MUTANT M2\n\t//',1)
open(p,'w').write(s)
PY
run_oracle "M2 prohibitedPath->stub" "TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey"

# M3: prohibitedValue always reports clean
python3 - "$SRC" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
s=s.replace('func prohibitedValue(value string) bool {',
            'func prohibitedValue(value string) bool {\n\treturn false // MUTANT M3\n\t//',1)
open(p,'w').write(s)
PY
run_oracle "M3 prohibitedValue->stub" "TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar"

# M7: validateProducerPins always accepts
python3 - "$SRC" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
s=s.replace('func validateProducerPins(value any) error {',
            'func validateProducerPins(value any) error {\n\treturn nil // MUTANT M7\n\t//',1)
open(p,'w').write(s)
PY
run_oracle "M7 validateProducerPins->nil" "E2E content_injection_rejected (unit-level may pass)"

# M8: relax prohibitedValue STRICTNESS - drop the phone regex only (per 20704 lesson:
#     mutate strictness, not just presence)
python3 - "$SRC" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
s=s.replace('return strings.Contains(lower, "://") || strings.Contains(lower, "@") || phoneValuePattern.MatchString(lower)',
            'return strings.Contains(lower, "://") || strings.Contains(lower, "@") // MUTANT M8 phone regex dropped',1)
open(p,'w').write(s)
PY
run_oracle "M8 drop phone regex only" "TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar"

# M9: relax the stem scan to exact-match instead of substring (the R2 defect, reintroduced)
python3 - "$SRC" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
s=s.replace('if strings.Contains(lower, stem) {','if lower == stem { // MUTANT M9 substring->exact',1)
open(p,'w').write(s)
PY
run_oracle "M9 stem substring->exact" "TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar"

echo
echo "=== restored source identical to candidate? ==="
git diff --quiet -- "$SRC" && echo "YES - source byte-identical to 89932217" || echo "NO - MUTATION LEFT BEHIND"
