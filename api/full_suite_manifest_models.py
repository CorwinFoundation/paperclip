"""Schema models and public types for full-suite execution manifests."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from scanners.live_probe_orchestrator import ScanRequest, ScanResult


FULL_SUITE_MANIFEST_VERSION = "full-suite-execution-manifest/v0.1"
FULL_SUITE_RUNNER_VERSION = "full-suite-runner/v0.1"

_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class FullSuiteManifestError(ValueError):
    pass


@dataclass(frozen=True)
class FullSuiteCase:
    case_id: str
    rubric_point_id: str
    required: bool = True
    approved_skip_reasons: tuple[str, ...] = ()

    def to_registry_row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "rubric_point_id": self.rubric_point_id,
            "required": self.required,
            "approved_skip_reasons": list(self.approved_skip_reasons),
        }


EvidenceUriResolver = Callable[[str, ScanRequest, ScanResult], str | None]
ManifestSigner = Callable[[dict[str, Any]], str]


def normalize_case(case: FullSuiteCase) -> FullSuiteCase:
    if not isinstance(case.case_id, str) or not _CASE_ID_RE.match(case.case_id):
        raise FullSuiteManifestError(f"invalid case_id: {case.case_id!r}")
    if not isinstance(case.rubric_point_id, str) or not case.rubric_point_id:
        raise FullSuiteManifestError(f"invalid rubric_point_id for {case.case_id!r}")
    return case
