#!/bin/bash
# QA BEAAA-23964 R2 — AC7 / Supreme Law Layer 2.
# The receipt self-reports "Candidate commit/tree: self / self", so byte
# identity has to be established by regenerating from two genuinely different
# trees with identical arguments, not by trusting the committed file.
set -u

CLAIM="BEAAA-23953 rejects every non-printable character in focus_claim and corrects the dangling canon citation; harness change measured at the +/-0 band, no scoring path touched."

run_side () {
  local tree="$1" out="$2"
  rm -rf "$out"; mkdir -p "$out"
  ( cd "$tree" && python3 scripts/release_gate_n5.py \
      --baseline 40abb5cb \
      --candidate HEAD \
      --phase development \
      --issue BEAAA-23953 \
      --cto-cosign-ref not-required-rule-execution \
      --focus-claim "$CLAIM" \
      --ticket-refs BEAAA-23953 BEAAA-23845 \
      --today 2026-08-17 \
      --out-dir "$out" ) > "$out/stdout.log" 2> "$out/stderr.log"
  echo "   exit=$? tree=$tree"
}

echo "=== side A: candidate worktree 4e68daac ==="
run_side /tmp/qa23964r2/cand /tmp/qa23964r2/outA
echo "=== side B: clean base worktree 40abb5cb ==="
run_side /tmp/qa23964r2/base /tmp/qa23964r2/outB

echo
echo "=== produced files ==="
ls -1 /tmp/qa23964r2/outA/*.md /tmp/qa23964r2/outA/*.json 2>/dev/null
ls -1 /tmp/qa23964r2/outB/*.md /tmp/qa23964r2/outB/*.json 2>/dev/null

echo
echo "=== sha256, both sides + the committed artifact ==="
sha256sum /tmp/qa23964r2/outA/*.md /tmp/qa23964r2/outA/*.json 2>/dev/null
sha256sum /tmp/qa23964r2/outB/*.md /tmp/qa23964r2/outB/*.json 2>/dev/null
sha256sum /tmp/qa23964r2/cand/artifacts/beaaa-23953/n5/2026-08-17-40abb5cb-vs-HEAD.md \
          /tmp/qa23964r2/cand/artifacts/beaaa-23953/n5/2026-08-17-40abb5cb-vs-HEAD.json 2>/dev/null

echo
echo "=== A vs B byte diff (empty = identical) ==="
diff -r /tmp/qa23964r2/outA /tmp/qa23964r2/outB --exclude=stdout.log --exclude=stderr.log && echo "   IDENTICAL"

echo
echo "=== stderr tails ==="
echo "--- A ---"; tail -5 /tmp/qa23964r2/outA/stderr.log
echo "--- B ---"; tail -5 /tmp/qa23964r2/outB/stderr.log
