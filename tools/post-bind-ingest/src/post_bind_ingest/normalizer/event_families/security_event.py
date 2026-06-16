"""`security_event` event family.

A security-relevant pattern was observed. Schema source:
`contracts/log-event-schema-v1.md @ e19cfb9` §Event family — `security_event`.

Note `detector_id` here is the *pattern-level* detector (e.g.
`secret_family@5740.1`), not a peril detector. Peril detector verdicts flow
through `claim_candidate_event`, which is detector-emitted and outside this
package's scope.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "security_event"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "pattern_family",
        "enum(secret_family, prompt_injection_marker, scope_escape_marker, runaway_loop_marker, signature_failure, kms_decrypt_failure, redaction_floor_triggered, other)",
        "yes", "clear", "backbond_normalizer",
    ),
    FieldSpec("severity", "enum(low, medium, high, critical)", "yes", "clear", "backbond_normalizer"),
    FieldSpec("evidence_event_id", "uuid", "yes", "clear", "backbond_normalizer"),
    FieldSpec("evidence_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("detector_id", "string", "yes", "clear", "backbond_normalizer",
              notes="pattern-level detector (e.g. secret_family@5740.1); NOT a peril detector"),
)
