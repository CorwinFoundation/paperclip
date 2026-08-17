import sys, re, pathlib; sys.path.insert(0, "/tmp/qa23963/cand")
from scripts import release_gate_n5 as g
import yaml
p = pathlib.Path("/tmp/qa23963/cand/rubric/rubric.yaml")
print("rubric.yaml exists:", p.exists())
doc = yaml.safe_load(p.read_text(encoding="utf-8"))

ids = set()
def walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("id", "rubric_point_id", "point_id") and isinstance(v, str):
                ids.add(v)
            walk(v)
    elif isinstance(node, list):
        for x in node: walk(x)
walk(doc)
print("distinct ids harvested:", len(ids))
bad = sorted(i for i in ids if not g.RUBRIC_POINT_RE.match(i))
good = sorted(i for i in ids if g.RUBRIC_POINT_RE.match(i))
print("ACCEPTED by regex:", len(good))
print("REJECTED by regex:", len(bad))
for b in bad[:60]: print("   REJECTED ->", repr(b))
print()
print("sample accepted:", good[:12])
