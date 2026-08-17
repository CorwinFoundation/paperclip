import sys, yaml
sys.path.insert(0, sys.argv[1])
from scripts import release_gate_n5 as G
print("MODULE:", G.__file__)


def validate(claim):
    return G._validate_receipt_provenance(
        issue="BEAAA-23953", cto_cosign_ref="not-required-rule-execution",
        focus_claim=claim, ticket_refs=["BEAAA-23953"])


def render_fm(claim):
    """Push the claim through the REAL renderer using the producer's own
    complete report fixture, then parse the emitted frontmatter."""
    from tests.test_release_gate_n5_beaaa10187 import _renderable_report
    md = G._render_markdown(_renderable_report(focus_claim=claim))
    _opening, block, _rest = md.split("---\n", 2)
    return yaml.safe_load(block), md


print("\n=== AC3: rejection + message names the character ===")
for label, claim in [
    ('double quote', 'BEAAA-1 fixes the "hardcoded" template.'),
    ('backslash',    'BEAAA-1 fixes the \\x41 template.'),
    ('newline',      'BEAAA-1 fixes it.\nowner: someone_else'),
    ('tab',          'BEAAA-1 fixes\tit.'),
    ('CR',           'BEAAA-1 fixes\rit.'),
    ('DEL 0x7F',     'BEAAA-1 fixes\x7fit.'),
]:
    try:
        validate(claim)
        print("  %-14s ACCEPTED  <-- NOT REJECTED" % label)
    except ValueError as e:
        print("  %-14s REJECTED: %s" % (label, str(e)[:95]))

print("\n=== AC4: round-trip through the REAL renderer ===")
claim = "BEAAA-23953: rejects a claim's unsafe chars (#1 priority, 100%) -- see [BEAAA-23845]."
fm, _ = render_fm(validate(claim)[2])
print("  identical:", fm["focus_claim"] == claim, "|", repr(fm["focus_claim"])[:90])

BASE_KEYS = {"title", "version", "status", "owner", "persona_audience", "phase",
             "canonical_terms_only", "focus_claim", "contracts_referenced", "ticket_refs",
             "brand_backbone_version", "brand_backbone_sha256", "brand_backbone_verified",
             "baseline_commit", "candidate_commit", "generated_at", "n", "cto_cosign_ref",
             "verdict", "prior_version", "proof_points_cited", "self_audit_note",
             "carrier_rule", "related_adr"}

print("\n=== ADVERSARIAL: chars PyYAML treats specially, NOT caught by (ord<0x20 or ==0x7F) ===")
for label, ch in [("U+0085 NEL", "\u0085"), ("U+2028 LINE SEP", "\u2028"),
                  ("U+2029 PARA SEP", "\u2029"), ("U+00A0 NBSP", "\u00a0"),
                  ("U+FEFF BOM", "\ufeff"), ("U+200B ZWSP", "\u200b")]:
    claim = "BEAAA-1 real claim." + ch + "owner: attacker"
    try:
        s = validate(claim)[2]
    except ValueError as e:
        print("  %-16s REJECTED by guard: %s" % (label, str(e)[:60]))
        continue
    try:
        fm, md = render_fm(s)
        rt = fm.get("focus_claim")
        same = (rt == claim)
        print("  %-16s ACCEPTED; frontmatter PARSED; round-trip-intact=%s" % (label, same))
        if not same:
            print("       claim in  : %r" % claim)
            print("       claim out : %r   <-- MUTATED" % rt)
            injected = sorted(set(fm) - BASE_KEYS)
            print("       injected keys: %s" % (injected or "none"))
    except yaml.YAMLError as e:
        print("  %-16s ACCEPTED; frontmatter FAILED TO PARSE (%s) <-- BROKEN RECEIPT"
              % (label, type(e).__name__))
