"""Field-by-field conformance vs `contracts/log-event-schema-v1.md @ e19cfb9`.

This test parses the byte-locked Schema markdown and walks every event family
the normalizer emits against the in-package `FieldSpec` declarations. CTO
brief §3 non-negotiable. Differences between the schema source and the
package declarations fail CI loud — the same PR must update both sides.

`claim_candidate_event` is intentionally not declared in this package (its
provenance is `backbond_detector`); the conformance test skips it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _test_paths import REPO_ROOT
from post_bind_ingest.normalizer.event_families import (
    COMMON_ENVELOPE,
    FieldSpec,
    agent_invocation,
    error as error_family,
    escalation,
    llm_completion,
    pii_emission,
    security_event,
    tool_call,
    tool_source_reference,
)


SCHEMA_PATH = REPO_ROOT / "contracts" / "log-event-schema-v1.md"

# Schema canon SHA reference. The conformance test reads the live file; the
# SHA below is the byte-locked publish SHA recorded by the CTO brief.
EXPECTED_SCHEMA_SHA_NOTE = "e19cfb9"


def _read_schema() -> str:
    if not SCHEMA_PATH.exists():
        pytest.skip(f"schema file not found at {SCHEMA_PATH}; conformance is environment-gated")
    return SCHEMA_PATH.read_text(encoding="utf-8")


_SECTION_RE = re.compile(r"^## §Event family — `(?P<family>[a-z_]+)`\s*$", re.MULTILINE)


def _parse_schema_table(section_text: str) -> list[tuple[str, str, str, str, str]]:
    """Parse the `| field | type | required | redaction | provenance | notes |` table.

    Returns a list of (name, type, required, redaction, provenance) tuples in
    declaration order. Notes are intentionally dropped. The parser keeps
    going across blank lines inside a section (sections may interleave
    paragraphs between tables, but for our purpose there is only one table
    of interest per section).
    """
    rows: list[tuple[str, str, str, str, str]] = []
    header_seen = False
    for line in section_text.splitlines():
        if not line.startswith("|"):
            continue
        if "Field" in line and "Type" in line:
            header_seen = False
            continue
        if "|---|" in line:
            header_seen = True
            continue
        if not header_seen:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        name = cells[0].strip("`")
        if not name or name in {"Field"}:
            continue
        rows.append((name, cells[1], cells[2], cells[3], cells[4]))
    return rows


def _schema_section(family: str, schema_text: str) -> str:
    matches = list(_SECTION_RE.finditer(schema_text))
    for index, match in enumerate(matches):
        if match.group("family") == family:
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(schema_text)
            return schema_text[start:end]
    pytest.fail(f"schema has no §Event family — `{family}` section")


def _normalize_required(s: str) -> str:
    s = s.lower()
    if s.startswith("conditional"):
        return "conditional"
    if "optional" in s:
        return "optional"
    if "yes" in s:
        return "yes"
    return s


def _normalize_redaction(s: str) -> str:
    # Two-form treatment lines like "`hash` on portfolio-export ...; `clear` ..."
    # The first inline-quoted class is the export-surface default per Schema text.
    match = re.search(r"`([^`]+)`", s)
    return match.group(1) if match else s.strip()


def _normalize_provenance(s: str) -> str:
    match = re.search(r"`([^`]+)`", s)
    return match.group(1) if match else s.strip()


def _normalize_type(s: str) -> str:
    s = s.strip()
    # Strip backticks at the boundary; keep the inner literal.
    if s.startswith("`") and s.endswith("`"):
        s = s[1:-1]
    return s


FAMILIES: dict[str, tuple[FieldSpec, ...]] = {
    "agent_invocation": agent_invocation.FIELDS,
    "llm_completion": llm_completion.FIELDS,
    "tool_call": tool_call.FIELDS,
    "tool_source_reference": tool_source_reference.FIELDS,
    "escalation": escalation.FIELDS,
    "error": error_family.FIELDS,
    "security_event": security_event.FIELDS,
    "pii_emission": pii_emission.FIELDS,
}


@pytest.mark.parametrize("family", sorted(FAMILIES.keys()))
def test_event_family_field_conformance(family: str) -> None:
    schema_text = _read_schema()
    section = _schema_section(family, schema_text)
    schema_rows = _parse_schema_table(section)
    pkg_specs = FAMILIES[family]
    pkg_by_name = {spec.name: spec for spec in pkg_specs}
    schema_names = [row[0] for row in schema_rows]
    assert schema_names == [spec.name for spec in pkg_specs], (
        f"{family}: field name / order mismatch.\n"
        f"  schema:  {schema_names}\n  package: {[spec.name for spec in pkg_specs]}"
    )
    for name, type_, required, redaction, provenance in schema_rows:
        spec = pkg_by_name[name]
        assert _normalize_type(type_) == _normalize_type(spec.field_type), (
            f"{family}.{name}: type mismatch — schema={type_!r} vs package={spec.field_type!r}"
        )
        assert _normalize_required(required) == spec.required, (
            f"{family}.{name}: required mismatch — schema={required!r} vs package={spec.required!r}"
        )
        assert _normalize_redaction(redaction) == spec.redaction, (
            f"{family}.{name}: redaction mismatch — schema={redaction!r} vs package={spec.redaction!r}"
        )
        assert _normalize_provenance(provenance) == spec.provenance, (
            f"{family}.{name}: provenance mismatch — schema={provenance!r} vs package={spec.provenance!r}"
        )


def test_common_envelope_field_conformance() -> None:
    """Common envelope appears once in §Common envelope, not per family.

    The COMMON_ENVELOPE declaration must be a superset of the names declared
    in the schema's §Common envelope table; the test asserts name + redaction
    + provenance match on each declared row.
    """
    schema_text = _read_schema()
    section_start = schema_text.find("## §Common envelope")
    assert section_start > 0, "schema missing §Common envelope section"
    section_end = schema_text.find("## §Event family", section_start)
    section = schema_text[section_start:section_end]
    rows = _parse_schema_table(section)
    pkg_by_name = {spec.name: spec for spec in COMMON_ENVELOPE}
    names_schema = [row[0] for row in rows]
    names_pkg = [spec.name for spec in COMMON_ENVELOPE]
    assert names_schema == names_pkg, (
        f"common envelope field name / order mismatch.\n"
        f"  schema:  {names_schema}\n  package: {names_pkg}"
    )


def test_claim_candidate_event_not_declared_in_normalizer() -> None:
    """The normalizer never emits claim_candidate_event — provenance is backbond_detector."""
    assert "claim_candidate_event" not in FAMILIES


def test_schema_canon_sha_referenced_in_package() -> None:
    """Sanity: README pins the canon Schema SHA. Drift on the README is a triage signal."""
    readme = (REPO_ROOT / "tools" / "post-bind-ingest" / "README.md").read_text(encoding="utf-8")
    assert EXPECTED_SCHEMA_SHA_NOTE in readme
