import subprocess, shutil, pathlib, sys
CAND = pathlib.Path("/tmp/qa23963/cand")
GEN  = CAND / "scripts/release_gate_n5.py"
ORIG = GEN.read_text(encoding="utf-8")

MUTANTS = [
 ("M1 re-hardcode PII literals (double-quoted) as emitted value",
  '"rubric_point_id": list(rubric_point_id),',
  '"rubric_point_id": ["GOVERNANCE.PII.HANDLING_ATTESTATION", "ROBUSTNESS.PII.SCAN_TIME_DETECTION"],'),
 ("M2 re-hardcode PII literals (SINGLE-quoted) - tests source-guard strictness",
  '"rubric_point_id": list(rubric_point_id),',
  "\"rubric_point_id\": ['GOVERNANCE.PII.HANDLING_ATTESTATION', 'ROBUSTNESS.PII.SCAN_TIME_DETECTION'],"),
 ("M3 regex accepts lowercase (IGNORECASE)",
  'RUBRIC_POINT_RE = re.compile(r"^[A-Z][A-Z0-9_]*(\\.[A-Z0-9_]+)+$")',
  'RUBRIC_POINT_RE = re.compile(r"^[A-Z][A-Z0-9_]*(\\.[A-Z0-9_]+)+$", re.IGNORECASE)'),
 ("M4 regex no longer requires a dot",
  'RUBRIC_POINT_RE = re.compile(r"^[A-Z][A-Z0-9_]*(\\.[A-Z0-9_]+)+$")',
  'RUBRIC_POINT_RE = re.compile(r"^[A-Z][A-Z0-9_.]*$")'),
 ("M5 drop sentinel+named-point combination check",
  'if RUBRIC_POINTS_NOT_EXERCISED in points and len(points) > 1:',
  'if False:'),
 ("M6 drop empty/absent rubric_point_id check",
  'if not points:',
  'if False:'),
 ("M7 CLI arg no longer required (defaults to PII pair)",
  '        "--rubric-point-id",\n        required=True,\n        nargs="+",',
  '        "--rubric-point-id",\n        required=False,\n        nargs="+",\n        default=["GOVERNANCE.PII.HANDLING_ATTESTATION"],'),
 ("M8 _render_markdown stops requiring rubric_point_id",
  'for key in ("issue", "focus_claim", "ticket_refs", "rubric_point_id")',
  'for key in ("issue", "focus_claim", "ticket_refs")'),
 ("M9 format validation skipped entirely",
  'if points != [RUBRIC_POINTS_NOT_EXERCISED]:',
  'if False:'),
]

results = []
for name, old, new in MUTANTS:
    if old not in ORIG:
        results.append((name, "ANCHOR-NOT-FOUND", "")); continue
    if ORIG.count(old) != 1:
        results.append((name, f"ANCHOR-AMBIGUOUS({ORIG.count(old)})", "")); continue
    GEN.write_text(ORIG.replace(old, new), encoding="utf-8")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q",
                        "tests/test_release_gate_n5_beaaa10187.py"],
                       cwd=CAND, capture_output=True, text=True, timeout=900)
    tail = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "no output"
    killed = p.returncode != 0
    failed = [l.split("::")[-1] for l in p.stdout.splitlines() if l.startswith("FAILED")]
    results.append((name, "KILLED" if killed else "*** SURVIVED ***", f"{tail} | {failed[:6]}"))
    GEN.write_text(ORIG, encoding="utf-8")

GEN.write_text(ORIG, encoding="utf-8")
print("=== MUTATION RESULTS ===")
for n, v, d in results:
    print(f"{v:18} {n}\n{'':18} {d}")
