"""Source-bound transport probes for agents that answer only permitted tasks.

These checks prove correlated transport and a small permitted task, not source
retrieval, ACL correctness, assessment coverage or a BackBond Score.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from harness.amazon_quick import _sha


@dataclass(frozen=True)
class FixtureProbe:
    metadata: dict
    question: str
    expected_reply: str

    def prompt(self, reference: str) -> str:
        if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", reference):
            raise ValueError("invalid_probe_reference")
        return self.question + " Connection test reference: " + reference + "."


def fixture_probe(profile: dict) -> FixtureProbe:
    """Use only the intact profile's common, explicitly permitted leave fact."""
    from evals.amazon_quick_assessment import digest

    fixture = profile["fixture"]
    if (profile.get("profile_sha256") != digest({k: v for k, v in profile.items() if k != "profile_sha256"})
            or profile.get("fixture_sha256") != digest(fixture)
            or set(fixture["source_readers"].get("leave.txt", [])) != set(fixture["roles"])
            or ["leave.txt", {"annual_leave_days": 20}] not in fixture["facts"]
            or "20 days" not in fixture["sources"]["leave.txt"]):
        raise ValueError("intact_common_fixture_probe_required")
    subject = ("the APPROVED synthetic employee handbook (leave.txt)" if profile["variant"] == "hr_finance"
               else "the fictional staff leave policy (leave.txt)")
    question = (f"According to {subject}, how many days of annual leave are employees entitled to? "
                "Reply with digits only, without words, punctuation, citations, Markdown, or quotation marks.")
    metadata = {"schema": "backbond-quick-fixture-probe/v1", "profile_id": profile["profile_id"],
        "profile_sha256": profile["profile_sha256"], "fixture_sha256": profile["fixture_sha256"],
        "source_name": "leave.txt", "source_sha256": _sha(fixture["sources"]["leave.txt"]),
        "question_sha256": _sha(question), "expected_reply_sha256": _sha("20")}
    return FixtureProbe(metadata, question, "20")
