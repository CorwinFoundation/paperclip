"""QA BEAAA-23964 R2 — replay the exact R1 blocker sweep against R2.

R1 result on these 32 code points was rejected=0, clean=0, unparseable=31,
silently_mutated=1 (U+0085 folding to a space). R2 must reject all 32 and must
name the offending code point in the message, which is the part the operator
needs when the character arrived from a mis-decoded Latin-1 paste.
"""

import re
import sys

sys.path.insert(0, "/tmp/qa23964r2/cand")
sys.path.insert(0, "/tmp/qa23964r2/cand/tests")

import yaml

from scripts import release_gate_n5 as m
from test_release_gate_n5_beaaa10187 import _renderable_report

C1 = list(range(0x80, 0xA0))
NAMED = {
    "double quote": '"',
    "backslash": "\\",
    "newline": "\n",
    "tab": "\t",
    "carriage return": "\r",
    "DEL": "\x7f",
    "vertical tab": "\x0b",
    "form feed": "\x0c",
    "NUL": "\x00",
    "LINE SEPARATOR U+2028": " ",
    "PARA SEPARATOR U+2029": " ",
}

rejected = named_cp = accepted = 0
failures = []

print("== AC3 part 1: the 32 C1 code points that defeated R1")
for cp in C1:
    claim = f"BEAAA-1 real claim.{chr(cp)}owner: attacker"
    try:
        m._validate_receipt_provenance(
            issue="BEAAA-23953",
            cto_cosign_ref=m.COSIGN_NOT_REQUIRED,
            focus_claim=claim,
            ticket_refs=["BEAAA-23953"],
        )
    except ValueError as exc:
        rejected += 1
        if re.search(rf"U\+{cp:04X}", str(exc)):
            named_cp += 1
        else:
            failures.append((cp, f"message does not name the code point: {exc}"))
        continue
    accepted += 1
    failures.append((cp, "ACCEPTED - R1 defect still open"))

print(f"   swept=32 rejected={rejected} accepted={accepted} message_names_code_point={named_cp}")
print(f"   R1 was: rejected=0 unparseable=31 silently_mutated=1")
print(f"   AC3-C1 VERDICT: {'PASS' if rejected == 32 and named_cp == 32 else 'FAIL ' + repr(failures[:5])}")

print()
print("== AC3 part 2: the individually named characters")
for label, ch in NAMED.items():
    claim = f"BEAAA-1 real claim.{ch}owner: attacker"
    try:
        m._validate_receipt_provenance(
            issue="BEAAA-23953",
            cto_cosign_ref=m.COSIGN_NOT_REQUIRED,
            focus_claim=claim,
            ticket_refs=["BEAAA-23953"],
        )
    except ValueError as exc:
        print(f"   rejected  {label:24s} :: {str(exc).split(';')[0][-56:]}")
        continue
    print(f"   ACCEPTED  {label:24s} :: FAIL")
    failures.append((label, "accepted"))

print()
print("== AC4: round-trip for an accepted claim with colon, hash, brackets, parens, apostrophe")
claim = (
    "BEAAA-23953: rejects a claim's unsafe characters "
    "(#1 priority, 100% of receipts) -- see [BEAAA-23845]."
)
md = m._render_markdown(_renderable_report(focus_claim=claim))
fm = yaml.safe_load(md.split("---\n", 2)[1])
print(f"   emitted line : {[l for l in md.splitlines() if l.startswith('focus_claim')][0]}")
print(f"   read back    : {fm['focus_claim']!r}")
print(f"   AC4 VERDICT  : {'PASS byte-identical' if fm['focus_claim'] == claim else 'FAIL drift'}")

print()
print(f"OVERALL: {'PASS' if not failures else 'FAIL ' + repr(failures)}")
