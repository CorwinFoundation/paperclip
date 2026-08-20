"""QA BEAAA-23964 R2 — independent verification of the focus_claim guard.

R1's defect was a range-shaped check. R2 claims a property-shaped one:
`str.isprintable()`. A property claim has to be tested over the whole domain,
not over the 261 code points the producer's own sweep visits. Two probes:

  P1  Completeness, over all 1,114,112 code points. PyYAML's own
      Reader.NON_PRINTABLE regex is the oracle for "would break the header".
      The guard is sufficient iff every code point PyYAML forbids is one
      Python calls non-printable. Uses the shipped PyYAML, not a remembered
      character list.

  P2  Soundness end to end, through the real renderer and yaml.safe_load,
      over every code point in 0x0000-0x2FFF plus the separators, BOM,
      noncharacters and a sample from each higher plane. For each: either the
      validator refuses it, or the receipt reads back byte-identical.

P2 also records whether an accepted claim can inject a sibling frontmatter key,
which is the finding that would raise F1 from corruption to false attribution.
"""

import sys
import unicodedata

sys.path.insert(0, "/tmp/qa23964r2/cand")

import yaml
from yaml.reader import Reader

from scripts import release_gate_n5 as m

sys.path.insert(0, "/tmp/qa23964r2/cand/tests")
from test_release_gate_n5_beaaa10187 import _renderable_report

MAX = 0x110000


def validate(claim):
    """Return the accepted claim, or None if the guard refuses it."""
    try:
        _issue, _cosign, accepted, _refs = m._validate_receipt_provenance(
            issue="BEAAA-23953",
            cto_cosign_ref=m.COSIGN_NOT_REQUIRED,
            focus_claim=claim,
            ticket_refs=["BEAAA-23953"],
        )
    except ValueError:
        return None
    return accepted


# ---------------------------------------------------------------- P1
print("== P1: is Python's non-printable set a superset of PyYAML's forbidden set?")
print(f"   oracle: yaml.reader.Reader.NON_PRINTABLE = {Reader.NON_PRINTABLE.pattern!r}")

yaml_forbids_python_prints = []
python_forbids_yaml_allows = 0
for cp in range(MAX):
    ch = chr(cp)
    try:
        yaml_bad = Reader.NON_PRINTABLE.search(ch) is not None
    except Exception:  # lone surrogate can raise inside re on some builds
        yaml_bad = True
    py_bad = not ch.isprintable()
    if yaml_bad and not py_bad:
        yaml_forbids_python_prints.append(cp)
    if py_bad and not yaml_bad:
        python_forbids_yaml_allows += 1

print(f"   code points PyYAML forbids but the guard ACCEPTS : {len(yaml_forbids_python_prints)}")
if yaml_forbids_python_prints:
    for cp in yaml_forbids_python_prints[:40]:
        print(f"     LEAK U+{cp:04X} {unicodedata.category(chr(cp))}")
print(f"   code points the guard rejects that PyYAML tolerates (stricter, safe): {python_forbids_yaml_allows}")
print(f"   P1 VERDICT: {'PASS - guard is sufficient over the full range' if not yaml_forbids_python_prints else 'FAIL - guard leaks'}")

# ---------------------------------------------------------------- P2
print()
print("== P2: end-to-end render + yaml.safe_load, real renderer")

suspects = list(range(0x0000, 0x3000))
suspects += [0x2028, 0x2029, 0x200B, 0x200E, 0xFEFF, 0xFFFE, 0xFFFF, 0xFFFD]
suspects += [0xD800, 0xDBFF, 0xDC00, 0xDFFF]
for plane in range(1, 17):  # sample each higher plane
    base = plane << 16
    suspects += [base, base + 0x0041, base + 0xFFFE, base + 0xFFFF]
suspects += [0x1F600, 0x1D400, 0xE0001, 0x10FFFF]
suspects = sorted(set(suspects))

BASE_KEYS = set(
    yaml.safe_load(
        m._render_markdown(_renderable_report(focus_claim="ordinary claim")).split("---\n", 2)[1]
    )
)
print(f"   baseline frontmatter keys derived from the renderer: {len(BASE_KEYS)}")

rejected = clean = 0
unparseable = []
mutated = []
injected = []
render_error = []

for cp in suspects:
    ch = chr(cp)
    claim = f"BEAAA-23953 keeps{ch}the claim readable."
    accepted = validate(claim)
    if accepted is None:
        rejected += 1
        continue
    try:
        md = m._render_markdown(_renderable_report(focus_claim=accepted))
        block = md.split("---\n", 2)[1]
    except Exception as exc:
        render_error.append((cp, type(exc).__name__))
        continue
    try:
        fm = yaml.safe_load(block)
    except yaml.YAMLError:
        unparseable.append(cp)
        continue
    if not isinstance(fm, dict):
        unparseable.append(cp)
        continue
    extra = set(fm) - BASE_KEYS
    if extra:
        injected.append((cp, sorted(extra)))
    if fm.get("focus_claim") != accepted:
        mutated.append((cp, repr(fm.get("focus_claim"))))
    if not extra and fm.get("focus_claim") == accepted:
        clean += 1

print(f"   swept={len(suspects)}  rejected={rejected}  clean_round_trip={clean}")
print(f"   accepted-but-unparseable   : {len(unparseable)} {[f'U+{c:04X}' for c in unparseable[:20]]}")
print(f"   accepted-but-value-mutated : {len(mutated)} {[(f'U+{c:04X}', v) for c, v in mutated[:20]]}")
print(f"   accepted-with-key-injection: {len(injected)} {[(f'U+{c:04X}', v) for c, v in injected[:20]]}")
print(f"   render raised              : {len(render_error)} {[(f'U+{c:04X}', e) for c, e in render_error[:20]]}")
bad = len(unparseable) + len(mutated) + len(injected)
print(f"   P2 VERDICT: {'PASS - every accepted claim round-trips byte-identical' if bad == 0 else f'FAIL - {bad} accepted claims corrupt the header'}")

# ---------------------------------------------------------------- P3
print()
print("== P3: does the guard reject ordinary claim text it should accept?")
ordinary = [
    "BEAAA-23953 rejects unsafe characters; no scoring path touched.",
    "Reduces false positives by 40% (see #1234) -- per Ari's review [v1.6].",
    "Mesure de l'ecart: rendement eleve, cout reduit.",
    "Fix: {a: b} | [c] & *d ! % @ ^ ~ ` = + < > ? / and 'quoted'.",
    "Bond spread widened 12bp -> 30bp across 3 issuers.",
]
for claim in ordinary:
    accepted = validate(claim)
    if accepted is None:
        print(f"   REJECTED (false positive?): {claim!r}")
    else:
        fm = yaml.safe_load(
            m._render_markdown(_renderable_report(focus_claim=accepted)).split("---\n", 2)[1]
        )
        ok = fm["focus_claim"] == claim
        print(f"   {'ok  ' if ok else 'DRIFT'} {claim[:58]!r}")
