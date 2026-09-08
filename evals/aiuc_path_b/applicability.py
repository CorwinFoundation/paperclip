"""Canonical capability-manifest applicability resolution.

BEAAA-29793 Rule A, per the CTO ruling on the ticket (comment `92f06a7f`):
`undeclared` is not `false`, the distinction must live in *data* rather than in
a formatted reason string, and the two resolvers that existed must converge
instead of each minting a vocabulary.

There were two. `evals.aiuc_path_b.base.run_family` resolved
`architecture_flags.get(flag, False)`, collapsing "the target declared no" with
"the target never answered" and writing the collapse into evidence as
`{flag}=False` — a declaration no target made.
`tools.registered_case_executor._resolve_applicability`, the resolver production
actually runs, already told those apart. `_declared_applicability_flag`'s
docstring states the invariant that the two "cannot disagree about which
surfaces a conditional family applies to"; on the undeclared case they did.

Both now call `resolve()` here, so the invariant is structural rather than
asserted. Renderings are byte-identical to the production resolver's previous
output: `always`, `{flag}=absent_capability_manifest`,
`{flag}=malformed_capability_manifest`, `{flag}=True`, and `{flag}={value!r}`
otherwise. The string is kept as a rendering of the structured result, never as
its carrier.

Provenance (CTO ruling, same comment): the tri-state is carried as
(value, source, attester). There are three sources on master and the third has
no attester at all — the registry mapping's `applicability.na_when_absent: true`
is a policy default, nobody's claim. If provenance were derived from channel
that default would silently inherit the registry's authority and become
indistinguishable from a declaration, which is the BEAAA-29788 hole reopened one
level down. So: **`policy_default` is never an attestation and always resolves
to `undeclared`.**

Execution semantics are unchanged by design (Rule C). `undeclared` gates a
family exactly as a declared decline does, so BEAAA-29788 stands and no absent
surface is exercised. What changes is only what the evidence says about why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# Declaration tri-state (plus the ungated case). This is the datum; the reason
# string is its rendering.
NOT_GATED = "not_gated"
DECLARED_TRUE = "declared_true"
DECLARED_FALSE = "declared_false"
UNDECLARED = "undeclared"

# Why a resolution landed where it did. The first two reuse the production
# resolver's existing vocabulary verbatim.
DETAIL_UNCONDITIONAL = "unconditional"
DETAIL_DECLARED = "declared"
DETAIL_ABSENT_MANIFEST = "absent_capability_manifest"
DETAIL_MALFORMED_MANIFEST = "malformed_capability_manifest"
DETAIL_FLAG_ABSENT = "flag_absent_from_manifest"
DETAIL_NON_BOOLEAN = "non_boolean_declaration"

# Who supplied the value. `operator_trusted` is the trusted-flags channel
# (`BACKBOND_TRUSTED_ARCHITECTURE_FLAGS_JSON`); an operator asserting an absence
# on the target's behalf is self-certification moved up one seat, not removed,
# so it is recorded as a distinct outcome.
SOURCE_TARGET_MANIFEST = "target_manifest"
SOURCE_OPERATOR_TRUSTED = "operator_trusted"
SOURCE_POLICY_DEFAULT = "policy_default"

ATTESTER_TARGET = "target"
ATTESTER_OPERATOR = "operator"
ATTESTER_NONE = "none"

_ATTESTER_BY_SOURCE = {
    SOURCE_TARGET_MANIFEST: ATTESTER_TARGET,
    SOURCE_OPERATOR_TRUSTED: ATTESTER_OPERATOR,
    SOURCE_POLICY_DEFAULT: ATTESTER_NONE,
}


@dataclass(frozen=True)
class Applicability:
    """A resolved applicability decision and everything that backs it."""

    applicable: bool
    declaration: str
    detail: str
    source: str
    attester: str
    flag: str | None
    reason: str

    def __iter__(self):
        """Preserve the resolver's historical ``(applicable, reason)`` API.

        Structured fields are available to new callers, while existing callers
        that unpack the production resolver continue to receive the exact pair
        returned before BEAAA-29793.
        """
        yield self.applicable
        yield self.reason

    @property
    def attested(self) -> bool:
        """Whether a named party actually claimed this value.

        Rule C's `not_applicable_attested` / `not_applicable_unattested` split
        reads this. It is deliberately not computable from the reason string,
        which is why the ruling required promoting the reason to data.
        """
        return (
            self.declaration in (DECLARED_TRUE, DECLARED_FALSE)
            and self.attester != ATTESTER_NONE
        )

    def as_evidence(self) -> dict[str, Any]:
        return {
            "flag": self.flag,
            "declaration": self.declaration,
            "detail": self.detail,
            "source": self.source,
            "attester": self.attester,
            "attested": self.attested,
            "reason": self.reason,
        }


def _unattested(flag: str, detail: str, reason: str) -> Applicability:
    return Applicability(
        applicable=False,
        declaration=UNDECLARED,
        detail=detail,
        source=SOURCE_POLICY_DEFAULT,
        attester=ATTESTER_NONE,
        flag=flag,
        reason=reason,
    )


def resolve(
    flag: str | None,
    capability_manifest: Any,
    *,
    source: str = SOURCE_TARGET_MANIFEST,
) -> Applicability:
    """Resolve one family's applicability against one capability manifest.

    Fail-closed: only an explicit `True` affirms a surface. An absent, null,
    malformed or non-boolean manifest cannot affirm one, so it yields N/A
    rather than a probe run — the safe direction, because the alternative is
    charging a verdict to a surface that was never shown to exist.
    """
    if flag is None:
        return Applicability(
            applicable=True,
            declaration=NOT_GATED,
            detail=DETAIL_UNCONDITIONAL,
            source=SOURCE_POLICY_DEFAULT,
            attester=ATTESTER_NONE,
            flag=None,
            reason="always",
        )

    if capability_manifest is None:
        return _unattested(
            flag, DETAIL_ABSENT_MANIFEST, f"{flag}={DETAIL_ABSENT_MANIFEST}"
        )
    if not isinstance(capability_manifest, Mapping):
        return _unattested(
            flag, DETAIL_MALFORMED_MANIFEST, f"{flag}={DETAIL_MALFORMED_MANIFEST}"
        )

    if flag not in capability_manifest:
        return _unattested(flag, DETAIL_FLAG_ABSENT, f"{flag}={None!r}")

    declared = capability_manifest[flag]
    if declared is True or declared is False:
        return Applicability(
            applicable=declared is True,
            declaration=DECLARED_TRUE if declared else DECLARED_FALSE,
            detail=DETAIL_DECLARED,
            source=source,
            attester=_ATTESTER_BY_SOURCE[source],
            flag=flag,
            reason=f"{flag}={declared}",
        )

    # A non-boolean is not a declaration. It renders exactly as the production
    # resolver rendered it, and resolves as the manifest defect it is.
    return _unattested(flag, DETAIL_NON_BOOLEAN, f"{flag}={declared!r}")
