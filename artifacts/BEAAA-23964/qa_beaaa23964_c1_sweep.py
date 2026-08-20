import sys, yaml
sys.path.insert(0, sys.argv[1])
from scripts import release_gate_n5 as G
from tests.test_release_gate_n5_beaaa10187 import _renderable_report


def validate(claim):
    return G._validate_receipt_provenance(
        issue="BEAAA-23953", cto_cosign_ref="not-required-rule-execution",
        focus_claim=claim, ticket_refs=["BEAAA-23953"])


def render_fm(claim):
    md = G._render_markdown(_renderable_report(focus_claim=claim))
    _o, block, _r = md.split("---\n", 2)
    return yaml.safe_load(block)


# Baseline key set taken from the producer's OWN fixture, so any extra key is
# genuinely injected rather than an artefact of my guessing the schema.
BASE_KEYS = set(render_fm("ordinary claim"))
print("baseline frontmatter keys (%d): %s\n" % (len(BASE_KEYS), sorted(BASE_KEYS)))

rejected, mutated, unparseable, clean = [], [], [], []
for cp in range(0x80, 0xA0):
    claim = "BEAAA-1 real claim." + chr(cp) + "owner: attacker"
    try:
        s = validate(claim)[2]
    except ValueError:
        rejected.append(cp)
        continue
    try:
        fm = render_fm(s)
    except yaml.YAMLError as e:
        unparseable.append((cp, type(e).__name__))
        continue
    if fm.get("focus_claim") != claim:
        mutated.append((cp, fm.get("focus_claim"), sorted(set(fm) - BASE_KEYS)))
    else:
        clean.append(cp)

print("C1 range U+0080-U+009F (32 code points), fed to the guard then the renderer:")
print("  REJECTED by guard      : %d  %s" % (len(rejected), [hex(c) for c in rejected]))
print("  accepted, receipt CLEAN: %d  %s" % (len(clean), [hex(c) for c in clean]))
print("  accepted, FRONTMATTER UNPARSEABLE: %d" % len(unparseable))
print("      %s" % [(hex(c), n) for c, n in unparseable][:8], "...") if unparseable else None
print("  accepted, claim SILENTLY MUTATED : %d" % len(mutated))
for cp, out, injected in mutated:
    print("      %s -> %r   injected keys: %s" % (hex(cp), out, injected or "NONE"))
