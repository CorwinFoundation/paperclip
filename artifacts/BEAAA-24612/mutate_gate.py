"""Delete or restore the BEAAA-24612 applicability gate, keeping EVALUATOR_SHA256 honest."""
import hashlib, re, sys

RCE = "tools/registered_case_executor.py"
TRO = "tools/tranche1_observational.py"

GATE = '''        applicable, reason = _resolve_applicability(test_id, registry_row)
        if not applicable:
            return _not_applicable_outcome(test_id, case_id, reason)
'''
STUB = '''        # MUTANT BEAAA-24612: applicability gate deleted on purpose
'''

def repin_evaluator():
    """Re-pin ONLY the EVALUATOR_SHA256 block (MAPPING_SHA256 shares its key names)."""
    new = hashlib.sha256(open(RCE, "rb").read()).hexdigest()
    s = open(TRO, encoding="utf-8").read()
    m = re.search(r"(EVALUATOR_SHA256 = \{)(.*?)(\n\})", s, re.S)
    block = re.sub(r'"[0-9a-f]{64}"', f'"{new}"', m.group(2))
    s = s[:m.start(2)] + block + s[m.end(2):]
    open(TRO, "w", encoding="utf-8").write(s)
    return new

mode = sys.argv[1]
src = open(RCE, encoding="utf-8").read()
if mode == "remove":
    assert src.count(GATE) == 1, "gate not present"
    open(RCE, "w", encoding="utf-8").write(src.replace(GATE, STUB))
elif mode == "restore":
    assert src.count(STUB) == 1, "stub not present"
    open(RCE, "w", encoding="utf-8").write(src.replace(STUB, GATE))
else:
    raise SystemExit("usage: mutate_gate.py remove|restore")
print(f"{mode}: evaluator re-pinned to {repin_evaluator()}")
