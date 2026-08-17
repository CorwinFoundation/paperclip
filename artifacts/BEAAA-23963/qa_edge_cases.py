import sys; sys.path.insert(0, "/tmp/qa23963/cand")
from scripts import release_gate_n5 as g

BASE = dict(issue="BEAAA-23957", cto_cosign_ref=g.COSIGN_NOT_REQUIRED,
            focus_claim="BEAAA-23957 parameterises the rubric points.",
            ticket_refs=["BEAAA-23957"])

CASES = [
  # (points, expect_reject?, note)
  ([],                                   True,  "acc3: empty list"),
  (["governance.pii"],                   True,  "acc3: lowercase id"),
  (["GOVERNANCE"],                       True,  "acc3: bare pillar, no dot"),
  (["none-exercised", "P1.1"],           True,  "acc3: sentinel + real point"),
  (["  "],                               True,  "whitespace-only"),
  (None,                                 True,  "None"),
  (["1.1"],                              True,  "starts with digit"),
  (["P1.1;DROP TABLE"],                   True,  "injection-ish chars"),
  (["GOVERNANCE..PII"],                  True,  "double dot / empty segment"),
  (["P1.1", "governance.pii"],           True,  "one good one bad -> whole call rejects"),
  (["Governance.Pii"],                   True,  "mixed case"),
  (["P1.1\nRubric points: FAKE"],        True,  "newline markdown injection"),
  (["GOVERNANCE.PII.HANDLING_ATTESTATION`"], True, "backtick markdown escape"),
  (["none-exercised", "none-exercised"], True,  "duplicated sentinel"),
  (["NONE-EXERCISED"],                   True,  "sentinel wrong case"),
  # --- these MUST be accepted ---
  (["P1.1"],                             False, "acc: P1.1 shape"),
  (["GOVERNANCE.PII.HANDLING_ATTESTATION"], False, "acc: real PII id still valid IF the run states it"),
  (["none-exercised"],                   False, "acc: sentinel alone"),
  (["P1.1", "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"], False, "acc: multiple"),
  ([" P1.1 "],                           False, "acc: whitespace stripped"),
]

fails = []
for pts, expect_reject, note in CASES:
    try:
        out = g._validate_receipt_provenance(**BASE, rubric_point_id=pts)
        got, err = "ACCEPTED", out[-1]
    except ValueError as e:
        got, err = "rejected", str(e)[:70]
    ok = (got == "rejected") == expect_reject
    if not ok: fails.append((note, pts, got, err))
    print(f"{'ok ' if ok else 'FAIL'} {got:9} {note:55} -> {err}")

print()
print("UNEXPECTED:", fails if fails else "none")
