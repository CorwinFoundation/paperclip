---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Pinned canonical-JSON serialization spec (RFC 8785 JCS) for ARE Scanner v1.2.0 snapshot_hash preimage; tamper-resistance lives or dies on byte-stability of this canonical form."
contracts_referenced: ["are-scanner-api-v1@v1.2.0"]
ticket_refs: ["BEAAA-1283", "BEAAA-1255"]
proof_points_cited: ["ADR-0017 §2.3 + §7 Q7c", "RFC 8785 JCS", "api/jcs.py canonicalize()"]
version: "v1.0"
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# Canonical-JSON serialization fixtures (ADR-0017 §7 Q7c)

These fixtures pin the byte-exact RFC 8785 JCS canonical form used as the
`snapshot_hash` preimage on the `POST /scanner/v1/rescans` envelope per
contract `are-scanner-api-v1@v1.2.0`. The CI gate is the unit test
`tests/test_canonical_serialization_beaaa1283.py` — it reads each
`{name}.input.json` from this directory, canonicalizes it via
`api.jcs.canonicalize`, and asserts the resulting bytes equal
`{name}.canonical.txt` and that `sha256({name}.canonical.txt)` equals
`{name}.expected-sha256`. Any drift in the JCS implementation, the fixture,
or the hash algorithm fails CI.

## Pinned spec (per ADR-0017 §7 Q7c)

- **Standard:** RFC 8785 (JSON Canonicalization Scheme — JCS).
- **Key ordering:** UTF-16 code-unit sequence (JCS §3.2.3). For the
  ASCII-identifier key space exercised by the v1.2.0 envelope, this
  coincides with ASCII alphabetical order.
- **Null handling:** present-with-`null` is serialized as `null`; absent
  keys are absent. These are distinct cases — the canonical form mirrors
  payload presence, never injects or suppresses keys.
- **Float precision:** ECMAScript 6 ToString (JCS §3.2.2.3) — shortest
  round-trip representation. Integer-valued floats emit without a trailing
  ".0". Non-finite floats are rejected.
- **String encoding:** UTF-8, no BOM. Minimal-escape rule of JCS §3.2.2.5 —
  the seven mandatory short escapes (`\"`, `\\`, `\b`, `\f`, `\n`, `\r`,
  `\t`) plus `\u00xx` for remaining C0 control codes; every other code
  point emitted as-is (so e.g. `café` and `漢字` stay as their UTF-8 bytes).
  Hex digits are lowercase.
- **Hash input scope:** the complete canonical JSON byte string of the full
  rescan response body, **excluding** the `snapshot_hash` field itself.
  The hash is computed before the `snapshot_hash` field is inserted.
- **Hash algorithm:** SHA-256, hex-encoded, prefixed `sha256:` →
  `sha256:<64 lowercase hex chars>`.

## Fixture inventory

| File pair | Coverage |
|---|---|
| `adr0017_q7c_minimal.*` | The exact fixture pinned in ADR-0017 §7 Q7c. Six top-level keys, all primitives, no nesting, ASCII-only. |
| `ws7_stress.*` | Stress fixture per BEAAA-1283 Scope #1: nested object with unsorted keys, present-`null` vs. absent key, escapable string (`\n`, `\t`, `\"`, `\\`, `\u0001`), UTF-8 non-ASCII (`café`, `π`, `漢字`, emoji), float with trailing-zero source (`0.87`). |

## Per-fixture files

For each fixture `{name}`, this directory contains:

- `{name}.input.json` — Source object, deliberately authored with unsorted
  keys / insignificant whitespace so the canonicalizer's normalization is
  the only thing that produces the canonical form.
- `{name}.canonical.txt` — Byte-exact JCS canonical form (UTF-8, no BOM,
  no trailing newline).
- `{name}.expected-sha256` — Lowercase hex SHA-256 of the canonical bytes
  (no `sha256:` prefix; CI test adds the prefix when comparing).

## Regenerating fixtures (one-shot, requires CTO co-sign)

Regeneration is itself a rule-change per `scanner-implementation-supervision-v1@v1.0`
because it touches the on-the-wire preimage of `snapshot_hash`. Do NOT
regenerate to "fix" a failing test — a failing test means the JCS
implementation or the fixture drifted and the chain of `snapshot_hash`
values committed to cold storage may not recompute. Escalate per the ADR.
