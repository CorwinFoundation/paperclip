"""Optional source inspection evidence. Never an admission or scoring decision."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ENGINE_REVISION = "54921c8c7c23008c8af2328d5ab492a904b6ece4"
ENGINE_VERSION = "2.12.0"
ROOT = Path(__file__).resolve().parents[1]
MAX_REPORT_BYTES = 2 * 1024 * 1024


def _digest_file(path: Path) -> tuple[str, int]:
    with path.open("rb") as handle:
        content = handle.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError("Advisory file-size limit exceeded")
    return hashlib.sha256(content).hexdigest(), len(content)


def inspect_skill(path: str | Path, *, python: str = sys.executable, timeout: int = 90) -> dict:
    """Inspect one local skill in a credential-free, network-denied child process.

    Findings never cause a blocking exit. Missing engines, parser failures and
    incomplete coverage remain explicit and must not look like a clean scan.
    """
    source = Path(path).resolve(strict=True)
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise ValueError("A local skill directory containing SKILL.md is required")
    if not 1 <= timeout <= 180:
        raise ValueError("timeout must be between 1 and 180 seconds")
    inventory = []
    total_bytes = 0
    for p in sorted(source.rglob("*")):
        if p.is_symlink() or not p.resolve().is_relative_to(source):
            raise ValueError("Skill symlinks and escaped paths are not supported")
        if p.is_file():
            digest, size = _digest_file(p)
            total_bytes += size
            if len(inventory) >= 2000 or total_bytes > 16 * 1024 * 1024:
                raise ValueError("Advisory inventory limit exceeded")
            inventory.append({"path": p.relative_to(source).as_posix(),
                              "sha256": digest})
    report = {"schema": "backbond.skill-inspection-advisory/v1", "advisory_only": True,
              "scored": False, "score_contribution": 0, "admission_effect": "none",
              "status": "unavailable", "review_required": True,
              "engine": {"name": "SkillSpector", "version": ENGINE_VERSION, "revision": ENGINE_REVISION},
              "source": {"path": str(source), "inventory": inventory},
              "findings": [], "limitations": [], "mode": "static_no_llm"}
    env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR") if k in os.environ}
    env.update(PYTHONUTF8="1", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            proc = subprocess.run([python, "-m", "harness.skill_inspection_worker", str(source)],
                                  cwd=ROOT, env=env, stdout=stdout, stderr=stderr,
                                  timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["limitations"].append(type(exc).__name__)
            return report
        if stdout.tell() > MAX_REPORT_BYTES:
            report.update(status="incomplete", limitations=["engine_report_size_limit"])
            return report
        stdout.seek(0)
        try:
            raw = json.loads(stdout.read().decode("utf-8"))
        except (UnicodeError, ValueError):
            report["limitations"].append("engine_output_unavailable_or_invalid")
            return report
    if proc.returncode not in (0, 1) or not isinstance(raw, dict):
        report["limitations"].append("engine_execution_failed")
        return report
    metadata, coverage = raw.get("metadata", {}), raw.get("analysis_completeness", {})
    if (not isinstance(metadata, dict) or not isinstance(coverage, dict)
            or not isinstance(raw.get("risk_assessment", {}), dict)):
        report["limitations"].append("engine_protocol_or_pin_mismatch")
        return report
    if (metadata.get("skillspector_version") != ENGINE_VERSION or metadata.get("llm_requested") is not False
            or metadata.get("inference_usage") or not isinstance(raw.get("issues"), list)):
        report["limitations"].append("engine_protocol_or_pin_mismatch")
        return report
    report.update(status="complete" if raw.get("execution_successful") is True and coverage.get("is_complete") is True else "incomplete",
                  findings=raw["issues"], coverage=coverage,
                  engine_recommendation=raw.get("risk_assessment", {}).get("recommendation"),
                  limitations=coverage.get("limitations", []))
    report["review_required"] = bool(report["findings"]) or report["status"] != "complete"
    # Bind the findings to the actual bytes before and after inspection.
    final_paths = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
    if final_paths != {entry["path"] for entry in inventory}:
        report.update(status="incomplete", review_required=True)
        report["limitations"].append("source_inventory_changed_during_inspection")
    for entry in inventory:
        p = source / entry["path"]
        if (p.is_symlink() or not p.resolve().is_relative_to(source) or not p.is_file()
                or _digest_file(p)[0] != entry["sha256"]):
            report.update(status="incomplete", review_required=True)
            report["limitations"].append("source_changed_during_inspection")
            break
    return report
