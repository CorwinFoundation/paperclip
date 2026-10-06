import json,subprocess,collections,sys
S='artifacts/beaaa-38570'
def show(rev,p): return [json.loads(l) for l in subprocess.run(['git','-C',R,'show',f'{rev}:{p}'],capture_output=True,text=True).stdout.splitlines() if l.strip()]
def reg(n): d=json.load(open(f'{S}/{n}.json')); r=d if isinstance(d,list) else d['rows']; return {x['candidate_dedupe_sha256']:x for x in r if x.get('candidate_dedupe_sha256')}
CLASS=('deterministic_field_assertion','paired_comparator','deterministic_conjunction')
def run(rev,suffix):
  inv=show(rev,'artifacts/beaaa-18720/unified-probe-inventory.jsonl'); fz={r['stable_case_id']:r for r in show(rev,'artifacts/beaaa-19218/vapi-2000-probe-authorization-freeze-v1.4.jsonl')}
  p,d=reg('reg-prod'+suffix),reg('reg-dev'+suffix)
  nc={r['stable_case_id'] for r in inv if not p[r['dedupe_sha256']].get('scoring_active') and not d[r['dedupe_sha256']].get('scoring_active')}
  out={}
  for sid,r in fz.items():
    js=r.get('judge_spec') or {}; k=js.get('type')
    if k not in CLASS: continue
    f=[js['field']] if js.get('field') else [a['field'] for a in js.get('assertions',[]) if a.get('field')]
    out[sid]={'kind':k,'fields':f,'family':r['family_id'],'never_captured':sid in nc}
  return len(nc),out
res={}
for rev,suf in (('d5490ad7','-d549'),('origin/master','')):
  n,o=run(rev,suf); res[rev]=o
  c=collections.Counter(v['kind'] for v in o.values() if v['never_captured'])
  print(rev,'never_captured',n,'class',len(o),'class∩nc',sum(c.values()),dict(c))
json.dump(res,open(f'{S}/joined.json','w'))
o=res['d5490ad7']; fc=collections.Counter(f for v in o.values() if v['never_captured'] for f in v['fields'])
print('distinct fields nc',len(fc)); print(' '.join(f'{f}:{n}' for f,n in fc.most_common()))
