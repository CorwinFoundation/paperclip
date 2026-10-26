"""Export a scope-review template or reconcile a local requirement evidence pack."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from requirement_assessment import PLAN_SCHEMA, build_requirement_assessment, directory_reader, load_registry, import_registered_case


def plan_template():
    now = datetime.now(timezone.utc)
    registry = load_registry()
    return {"schema": PLAN_SCHEMA, "assessment_id": "",
            "scope": {"target_id": "", "deployment_id": "", "snapshot_sha256": ""},
            "approval": {"reviewer": "", "basis": "", "approved_at": None},
            "valid_from": now.isoformat(), "valid_until": (now + timedelta(days=7)).isoformat(),
            "controls": [{"framework_id": f["framework_id"], "control_id": c["control_id"],
                          "mapping_version": f["mapping_version"], "applicability": "unresolved",
                          "justification": "Scope review pending.", "required_tests": []}
                         for f in registry["frameworks"] for c in f["controls"]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-plan", action="store_true")
    parser.add_argument("--import-registered-case", type=Path)
    parser.add_argument("--binding-index", type=int)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--evaluated-at")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.init_plan and any((args.plan, args.bundle, args.artifacts, args.import_registered_case)):
        parser.error("--init-plan cannot consume evidence")
    if args.import_registered_case and (not args.plan or not args.artifacts or args.binding_index is None):
        parser.error("import requires --plan, --artifacts and --binding-index")
    if not args.init_plan and not args.import_registered_case and not all((args.plan, args.bundle, args.artifacts)):
        parser.error("projection requires --plan, --bundle and --artifacts")
    try:
        if args.init_plan:
            result = plan_template()
        elif args.import_registered_case:
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            reference = import_registered_case(args.import_registered_case.read_bytes(), plan=plan,
                        binding_index=args.binding_index, artifact_directory=args.artifacts)
            result = json.loads(args.bundle.read_text(encoding="utf-8")) if args.bundle else {
                "assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": [], "documents": []}
            if result.get("assessment_id") != plan["assessment_id"] or result.get("scope") != plan["scope"]:
                raise ValueError("bundle scope differs")
            result["evidence_refs"].append(reference)
        else:
            result = build_requirement_assessment(
                json.loads(args.bundle.read_text(encoding="utf-8")),
                plan=json.loads(args.plan.read_text(encoding="utf-8")),
                artifact_reader=directory_reader(args.artifacts), evaluated_at=args.evaluated_at)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    except (ValueError, TypeError, KeyError, OSError, IndexError, AttributeError):
        parser.exit(2, "Requirement evidence input/output is invalid or unavailable; no assessment was issued.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
