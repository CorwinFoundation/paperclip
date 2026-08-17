"""BEAAA-20177 — production CEL port of the reference growth-agent rule pack.

Parity is proven against the reference appliance's own declarative engine, loaded
from the pinned baseline tree, rather than against a restatement of it here.  If the
baseline pack and the production pack ever disagree on a synthetic corpus, these
tests fail rather than the disagreement shipping.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from determination.growth_cel import (
    EXPECTED_INTENTS,
    GROWTH_FORBIDDEN_KEYS,
    POSTURE,
    RELEASED_VERSION,
    RUBRIC_POINTS,
    SOURCE_STATES,
    GrowthCelError,
    GrowthCelPack,
    reject_growth_forbidden_keys,
)
from determination.growth_parity import (
    GROWTH_RULE_TO_INTENT,
    count_determinations,
    reduce_to_evidence,
)
from determination.voice_cel import CLOSED_SUBSET


COMMITMENT_REF = "sha256:" + "0" * 64

REPO_ROOT = Path(__file__).resolve().parents[1]
APPLIANCE = (
    REPO_ROOT
    / "ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance"
)
REFERENCE_PACK = APPLIANCE / "appliance/rules/growth_agent_pack.yaml"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reference_engine():
    """The pinned reference appliance's declarative engine — the port's source of truth."""
    return _load_module("_bb_ref_engine", APPLIANCE / "appliance/rules/engine.py")


@pytest.fixture(scope="module")
def reference_pipeline():
    return _load_module("_bb_ref_pipeline", APPLIANCE / "appliance/pipeline.py")


@pytest.fixture(scope="module")
def reference_rules(reference_engine):
    return reference_engine.RuleSet.from_yaml(str(REFERENCE_PACK))


@pytest.fixture(scope="module")
def pack() -> GrowthCelPack:
    return GrowthCelPack.load()


# --------------------------------------------------------------------------- #
# Pack shape, pin, and posture
# --------------------------------------------------------------------------- #


def test_released_pin_posture_and_intent_coverage(pack):
    assert pack.rule_pack_version == RELEASED_VERSION
    assert {rule.intent_id for rule in pack.rules} == EXPECTED_INTENTS
    assert len(pack.rules) == 6


def test_every_reference_rule_has_exactly_one_production_intent(reference_rules):
    reference_ids = {rule.id for rule in reference_rules.rules}
    assert reference_ids == set(GROWTH_RULE_TO_INTENT)
    assert set(GROWTH_RULE_TO_INTENT.values()) == EXPECTED_INTENTS
    assert len(set(GROWTH_RULE_TO_INTENT.values())) == len(GROWTH_RULE_TO_INTENT)


def test_port_adds_no_expression_capability(released_doc):
    """Every growth expression compiles against the voice lane's closed subset."""
    for item in released_doc["rules"]:
        assert CLOSED_SUBSET.fullmatch(item["expression"]) is not None


def test_rubric_points_are_growth_labels(pack):
    assert set(RUBRIC_POINTS) == EXPECTED_INTENTS
    assert all(label.startswith("GROWTH.") for label in RUBRIC_POINTS.values())
    assert len(set(RUBRIC_POINTS.values())) == len(RUBRIC_POINTS)


# --------------------------------------------------------------------------- #
# Truth tables: positive / negative / boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("intent_id", sorted(EXPECTED_INTENTS))
@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (0, 1, "pass"),
        (0, 0, "pass"),
        (1, 1, "fail"),
        (2, 1, "fail"),
        (9999, 10000, "fail"),
        (None, 1, "indeterminate"),
        (0, None, "indeterminate"),
        (None, None, "indeterminate"),
    ],
)
def test_truth_tables(pack, intent_id, left, right, expected):
    rule = next(r for r in pack.rules if r.intent_id == intent_id)
    evidence = {}
    if left is not None:
        evidence[rule.violation_field] = left
    if right is not None:
        evidence[rule.baseline_field] = right
    result = pack.evaluate(
        intent_id, evidence, commitment_ref=COMMITMENT_REF, source_state="healthy"
    )
    assert result["verdict"] == expected
    assert result["rule_pack_version"] == RELEASED_VERSION
    assert result["rubric_point_id"] == RUBRIC_POINTS[intent_id]
    assert result["posture"] == POSTURE


@pytest.mark.parametrize("intent_id", sorted(EXPECTED_INTENTS))
def test_zero_to_one_is_the_only_verdict_boundary(pack, intent_id):
    rule = next(r for r in pack.rules if r.intent_id == intent_id)

    def verdict(left):
        return pack.evaluate(
            intent_id,
            {rule.violation_field: left, rule.baseline_field: 100},
            commitment_ref=COMMITMENT_REF,
            source_state="healthy",
        )["verdict"]

    assert verdict(0) == "pass"
    assert verdict(1) == "fail"
    assert all(verdict(n) == "fail" for n in (2, 50, 100, 101))


@pytest.mark.parametrize("bad", [-1, True, False, 1.0, "1", [1], {"n": 1}])
def test_non_counter_evidence_fails_closed(pack, bad):
    rule = pack.rules[0]
    with pytest.raises(GrowthCelError, match="non-negative integers"):
        pack.evaluate(
            rule.intent_id,
            {rule.violation_field: bad, rule.baseline_field: 1},
            commitment_ref=COMMITMENT_REF,
            source_state="healthy",
        )


def test_unknown_intent_and_unknown_evidence_field_fail_closed(pack):
    rule = pack.rules[0]
    with pytest.raises(GrowthCelError, match="unknown intent_id"):
        pack.evaluate("growth.not_a_peril", {}, commitment_ref=COMMITMENT_REF, source_state="healthy")
    with pytest.raises(GrowthCelError, match="outside the rule allowlist"):
        pack.evaluate(
            rule.intent_id,
            {rule.violation_field: 0, rule.baseline_field: 1, "vendor_blob": 1},
            commitment_ref=COMMITMENT_REF,
            source_state="healthy",
        )


# --------------------------------------------------------------------------- #
# No-content invariant
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("forbidden", sorted(GROWTH_FORBIDDEN_KEYS))
@pytest.mark.parametrize("depth", [0, 2])
def test_growth_content_vocabulary_rejected_at_every_depth(forbidden, depth):
    value = {forbidden: "fixture-secret"}
    for index in range(depth):
        value = {f"level_{index}": [value]}
    with pytest.raises(GrowthCelError, match="forbidden key"):
        reject_growth_forbidden_keys(value)


def test_growth_set_is_a_superset_of_the_voice_set():
    from determination.growth_cel import FORBIDDEN_KEYS as GROWTH_SET
    from determination.voice_cel import FORBIDDEN_KEYS as VOICE_SET

    assert VOICE_SET <= GROWTH_SET
    with pytest.raises(GrowthCelError, match="forbidden key"):
        reject_growth_forbidden_keys({"outer": [{"CRM-Record": "x"}]})


def test_output_is_closed_and_carries_no_content(pack):
    rule = next(r for r in pack.rules if r.intent_id == "crm_data_misuse")
    result = pack.evaluate(
        "crm_data_misuse",
        {rule.violation_field: 3, rule.baseline_field: 12},
        commitment_ref=COMMITMENT_REF,
        source_state="healthy",
    )
    assert set(result) == {
        "rule_pack_version",
        "intent_id",
        "rubric_point_id",
        "posture",
        "verdict",
        "observed_count",
        "commitment_ref",
        "health",
    }
    assert "expression" not in result
    reject_growth_forbidden_keys(result)


@pytest.mark.parametrize(
    "commitment_ref",
    ["", None, "sha256:", "sha256:abcd", "sha256:" + "g" * 64, "SHA256:" + "0" * 64,
     "sha256:" + "0" * 64 + "\nlead email victim@example.com"],
)
def test_commitment_ref_validation_fails_closed(pack, commitment_ref):
    rule = pack.rules[0]
    with pytest.raises(GrowthCelError, match="commitment_ref"):
        pack.evaluate(
            rule.intent_id,
            {rule.violation_field: 0, rule.baseline_field: 1},
            commitment_ref=commitment_ref,
            source_state="healthy",
        )


@pytest.mark.parametrize("source_state", ["", None, "Healthy", "unknown", "healthy\nrecord=secret"])
def test_source_state_validation_fails_closed(pack, source_state):
    rule = pack.rules[0]
    with pytest.raises(GrowthCelError, match="source_state"):
        pack.evaluate(
            rule.intent_id,
            {rule.violation_field: 0, rule.baseline_field: 1},
            commitment_ref=COMMITMENT_REF,
            source_state=source_state,
        )


@pytest.mark.parametrize("source_state", sorted(SOURCE_STATES))
def test_every_continuity_state_is_accepted(pack, source_state):
    rule = pack.rules[0]
    result = pack.evaluate(
        rule.intent_id,
        {rule.violation_field: 0, rule.baseline_field: 1},
        commitment_ref=COMMITMENT_REF,
        source_state=source_state,
    )
    assert result["health"] == {"source_state": source_state}


# --------------------------------------------------------------------------- #
# Monitor-only posture — enforce:true must be structurally unrepresentable
# --------------------------------------------------------------------------- #


def _write_pack(tmp_path: Path, doc: dict, name: str = "candidate.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.fixture
def released_doc() -> dict:
    from determination.growth_cel import RULE_PACK_PATH

    return json.loads(RULE_PACK_PATH.read_text(encoding="utf-8"))


def test_enforce_cannot_be_expressed_on_a_rule(tmp_path, released_doc):
    doc = copy.deepcopy(released_doc)
    doc["rules"][0]["enforce"] = True
    with pytest.raises(GrowthCelError, match="rule has unknown keys"):
        GrowthCelPack.load(_write_pack(tmp_path, doc))


def test_enforce_cannot_be_expressed_at_pack_level(tmp_path, released_doc):
    doc = copy.deepcopy(released_doc)
    doc["enforce"] = True
    with pytest.raises(GrowthCelError, match="unknown top-level keys"):
        GrowthCelPack.load(_write_pack(tmp_path, doc))


@pytest.mark.parametrize("posture", ["enforce", "block", "enforcing", "monitor", True])
def test_posture_must_be_monitor_only(tmp_path, released_doc, posture):
    doc = copy.deepcopy(released_doc)
    doc["posture"] = posture
    with pytest.raises(GrowthCelError, match="monitor_only"):
        GrowthCelPack.load(_write_pack(tmp_path, doc))


def test_widened_expression_is_rejected(tmp_path, released_doc):
    doc = copy.deepcopy(released_doc)
    doc["rules"][0]["expression"] = 'e.unconfirmed_spend_count > 0 ? "fail" : "pass"'
    with pytest.raises(GrowthCelError, match="outside the closed CEL subset"):
        GrowthCelPack.load(_write_pack(tmp_path, doc))


# --------------------------------------------------------------------------- #
# Version pin, promotion, rollback
# --------------------------------------------------------------------------- #


def test_pack_at_an_unpinned_version_does_not_load(tmp_path, released_doc):
    doc = copy.deepcopy(released_doc)
    doc["rule_pack_version"] = "growth-perils-cel@1.1.0"
    with pytest.raises(GrowthCelError, match="not the expected pin"):
        GrowthCelPack.load(_write_pack(tmp_path, doc))


def test_dropping_a_pack_on_disk_does_not_promote_it(tmp_path, released_doc):
    """Promotion is an explicit call-site act, not a file appearing at a path."""
    doc = copy.deepcopy(released_doc)
    doc["rule_pack_version"] = "growth-perils-cel@1.1.0"
    doc["rules"].append(
        {
            "intent_id": "affiliate_disclosure_missing",
            "expression": '!has(e.missing_affiliate_disclosure_count) || !has(e.affiliate_message_count) ? "indeterminate" : e.missing_affiliate_disclosure_count > 0 ? "fail" : "pass"',
        }
    )
    path = _write_pack(tmp_path, doc)
    with pytest.raises(GrowthCelError, match="not the expected pin"):
        GrowthCelPack.load(path)
    promoted = GrowthCelPack.load(path, expected_version="growth-perils-cel@1.1.0")
    assert len(promoted.rules) == 7


def _verdict_table(pack: GrowthCelPack) -> str:
    rows = []
    for intent_id in sorted(EXPECTED_INTENTS):
        rule = next(r for r in pack.rules if r.intent_id == intent_id)
        for left, right in ((0, 5), (1, 5), (7, 5)):
            rows.append(
                pack.evaluate(
                    intent_id,
                    {rule.violation_field: left, rule.baseline_field: right},
                    commitment_ref=COMMITMENT_REF,
                    source_state="healthy",
                )
            )
    return json.dumps(rows, sort_keys=True)


def test_promotion_preserves_every_released_intent(tmp_path, released_doc, pack):
    """A candidate may add perils; it may not silently move an existing verdict."""
    doc = copy.deepcopy(released_doc)
    doc["rule_pack_version"] = "growth-perils-cel@1.1.0"
    doc["rules"].append(
        {
            "intent_id": "affiliate_disclosure_missing",
            "expression": '!has(e.missing_affiliate_disclosure_count) || !has(e.affiliate_message_count) ? "indeterminate" : e.missing_affiliate_disclosure_count > 0 ? "fail" : "pass"',
        }
    )
    promoted = GrowthCelPack.load(
        _write_pack(tmp_path, doc), expected_version="growth-perils-cel@1.1.0"
    )
    released_rows = json.loads(_verdict_table(pack))
    promoted_rows = json.loads(_verdict_table(promoted))
    for released_row, promoted_row in zip(released_rows, promoted_rows):
        assert released_row["verdict"] == promoted_row["verdict"]
        assert released_row["intent_id"] == promoted_row["intent_id"]
        assert released_row["rubric_point_id"] == promoted_row["rubric_point_id"]


def test_promoting_an_unlabelled_intent_cannot_emit_a_determination(tmp_path, released_doc):
    doc = copy.deepcopy(released_doc)
    doc["rule_pack_version"] = "growth-perils-cel@1.1.0"
    doc["rules"].append(
        {
            "intent_id": "affiliate_disclosure_missing",
            "expression": '!has(e.missing_affiliate_disclosure_count) || !has(e.affiliate_message_count) ? "indeterminate" : e.missing_affiliate_disclosure_count > 0 ? "fail" : "pass"',
        }
    )
    promoted = GrowthCelPack.load(
        _write_pack(tmp_path, doc), expected_version="growth-perils-cel@1.1.0"
    )
    with pytest.raises(GrowthCelError, match="no rubric point label"):
        promoted.evaluate(
            "affiliate_disclosure_missing",
            {"missing_affiliate_disclosure_count": 1, "affiliate_message_count": 4},
            commitment_ref=COMMITMENT_REF,
            source_state="healthy",
        )


def test_a_candidate_dropping_a_released_intent_is_rejected(tmp_path, released_doc):
    doc = copy.deepcopy(released_doc)
    doc["rule_pack_version"] = "growth-perils-cel@1.1.0"
    doc["rules"] = [r for r in doc["rules"] if r["intent_id"] != "impersonation_signal"]
    with pytest.raises(GrowthCelError, match="missing ported peril intents"):
        GrowthCelPack.load(
            _write_pack(tmp_path, doc), expected_version="growth-perils-cel@1.1.0"
        )


def test_rollback_restores_the_released_verdict_table_exactly(tmp_path, released_doc, pack):
    """Rollback is reload-the-pin; it must reproduce the pre-promotion table byte for byte."""
    before = _verdict_table(pack)
    doc = copy.deepcopy(released_doc)
    doc["rule_pack_version"] = "growth-perils-cel@1.1.0"
    GrowthCelPack.load(_write_pack(tmp_path, doc), expected_version="growth-perils-cel@1.1.0")
    rolled_back = GrowthCelPack.load()
    assert rolled_back == pack
    assert _verdict_table(rolled_back) == before


# --------------------------------------------------------------------------- #
# Synthetic parity against the pinned reference appliance
# --------------------------------------------------------------------------- #

# Fake content and fake PII on purpose: the point is that none of it reaches production.
SYNTHETIC_EVENTS = [
    {"type": "social_post", "id": "s1", "tenant_id": "smb-alpha", "agent_id": "social",
     "ts": "2026-08-17T09:00:00Z", "channel": "linkedin",
     "text": "New feature launch — read the blog.", "has_unsubscribe": True,
     "consent": "opt_in", "channel_authorized": True},
    {"type": "social_post", "id": "s2", "tenant_id": "smb-alpha", "agent_id": "social",
     "ts": "2026-08-17T09:05:00Z", "channel": "x",
     "text": "Risk-free! 100% guarantee you get rich.", "has_unsubscribe": True,
     "consent": "opt_in", "channel_authorized": True},
    {"type": "meeting_booked", "id": "s3", "tenant_id": "smb-beta", "agent_id": "sdr",
     "ts": "2026-08-17T09:10:00Z", "recipient": "buyer@example.com",
     "confirmed": False, "amount": 250},
    {"type": "meeting_booked", "id": "s4", "tenant_id": "smb-beta", "agent_id": "sdr",
     "ts": "2026-08-17T09:11:00Z", "recipient": "buyer2@example.com",
     "confirmed": True, "amount": 500},
    {"type": "dm", "id": "s5", "tenant_id": "smb-beta", "agent_id": "community",
     "ts": "2026-08-17T09:12:00Z", "channel": "reddit", "text": "Buy now.",
     "has_unsubscribe": False, "consent": "none", "recipient": "u/someone",
     "channel_authorized": True},
    {"type": "crm_update", "id": "s6", "tenant_id": "smb-beta", "agent_id": "ops",
     "ts": "2026-08-17T09:15:00Z", "record_count": 4200,
     "customer_data": {"ssn": "078-05-1120", "email": "victim@example.com"}},
    {"type": "community_message", "id": "s7", "tenant_id": "smb-alpha", "agent_id": "community",
     "ts": "2026-08-17T09:20:00Z", "channel": "discord",
     "text": "I'm human, not a bot, speaking personally.", "channel_authorized": True},
    {"type": "social_post", "id": "s8", "tenant_id": "smb-alpha", "agent_id": "social",
     "ts": "2026-08-17T09:25:00Z", "channel": "tiktok", "text": "Product update.",
     "has_unsubscribe": True, "consent": "opt_in", "channel_authorized": False},
]

BENIGN_EVENTS = [
    {"type": "social_post", "id": "b1", "tenant_id": "smb-alpha", "agent_id": "social",
     "ts": "2026-08-17T10:00:00Z", "channel": "linkedin", "text": "Weekly product notes.",
     "has_unsubscribe": True, "consent": "opt_in", "channel_authorized": True},
    {"type": "meeting_booked", "id": "b2", "tenant_id": "smb-beta", "agent_id": "sdr",
     "ts": "2026-08-17T10:05:00Z", "confirmed": True, "amount": 100},
    {"type": "crm_update", "id": "b3", "tenant_id": "smb-beta", "agent_id": "ops",
     "ts": "2026-08-17T10:10:00Z", "record_count": 12},
    {"type": "newsletter_generated", "id": "b4", "tenant_id": "smb-alpha", "agent_id": "newsletter",
     "ts": "2026-08-17T10:15:00Z", "body": "Roundup.", "has_unsubscribe": True,
     "consent": "opt_in", "channel_authorized": True},
]

#: Which action_class each reference rule is eligible over, for the denominators.
BASELINE_CLASS = {
    "growth.unauthorized_spend": "transaction",
    "growth.brand_safety": "message",
    "growth.outreach_compliance": "message",
    "growth.crm_data_misuse": "data_access",
    "growth.impersonation": "message",
    "growth.off_scope_channel": "message",
}


def _run_reference(reference_pipeline, reference_rules, raw_events):
    """Evaluate the reference pack locally, exactly as the appliance does."""
    working = [reference_pipeline.normalize(dict(event)) for event in raw_events]
    determinations = []
    for event in working:
        determinations.extend(reference_rules.determinations(event))
    baselines = {
        rule_id: sum(1 for e in working if e["action_class"] == action_class)
        for rule_id, action_class in BASELINE_CLASS.items()
    }
    return working, determinations, baselines


def _production_verdicts(pack, reference_pipeline, reference_rules, raw_events):
    _, determinations, baselines = _run_reference(reference_pipeline, reference_rules, raw_events)
    evidence = reduce_to_evidence(
        pack,
        evaluated_rule_ids=GROWTH_RULE_TO_INTENT,
        match_counts=count_determinations(determinations),
        baseline_counts=baselines,
    )
    verdicts = {
        intent_id: pack.evaluate(
            intent_id, entry, commitment_ref=COMMITMENT_REF, source_state="healthy"
        )["verdict"]
        for intent_id, entry in evidence.items()
    }
    return determinations, verdicts


def test_synthetic_parity_reference_matches_production(pack, reference_pipeline, reference_rules):
    determinations, verdicts = _production_verdicts(
        pack, reference_pipeline, reference_rules, SYNTHETIC_EVENTS
    )
    matched = {d["rule_id"] for d in determinations}
    assert matched, "synthetic corpus must exercise the reference pack"
    for rule_id, intent_id in GROWTH_RULE_TO_INTENT.items():
        expected = "fail" if rule_id in matched else "pass"
        assert verdicts[intent_id] == expected, f"parity break on {rule_id}"


def test_synthetic_corpus_exercises_every_ported_peril(pack, reference_pipeline, reference_rules):
    determinations, verdicts = _production_verdicts(
        pack, reference_pipeline, reference_rules, SYNTHETIC_EVENTS
    )
    assert {d["rule_id"] for d in determinations} == set(GROWTH_RULE_TO_INTENT)
    assert set(verdicts.values()) == {"fail"}


def test_benign_corpus_produces_no_false_positives(pack, reference_pipeline, reference_rules):
    determinations, verdicts = _production_verdicts(
        pack, reference_pipeline, reference_rules, BENIGN_EVENTS
    )
    assert determinations == []
    assert set(verdicts) == EXPECTED_INTENTS
    assert set(verdicts.values()) == {"pass"}


@pytest.mark.parametrize(
    ("record_count", "expected"), [(499, "pass"), (500, "pass"), (501, "fail"), (4200, "fail")]
)
def test_crm_bulk_threshold_boundary_holds_across_the_port(
    pack, reference_pipeline, reference_rules, record_count, expected
):
    """The reference threshold is `> 500`; the port must not move it by a single record."""
    events = [
        {"type": "crm_update", "id": "t1", "tenant_id": "smb-beta", "agent_id": "ops",
         "ts": "2026-08-17T11:00:00Z", "record_count": record_count}
    ]
    _, verdicts = _production_verdicts(pack, reference_pipeline, reference_rules, events)
    assert verdicts["crm_data_misuse"] == expected


#: Which production intent each synthetic event must land on, written out literally.
#: Deriving this from GROWTH_RULE_TO_INTENT would route both sides of the assertion
#: through the mapping under test, so a transposed mapping would still "agree" with
#: itself.  These are the independent anchor.
EVENT_EXPECTED_INTENT = {
    "s1": None,
    "s2": "brand_safety_signal",
    "s3": "unauthorized_spend_without_confirmation",
    "s4": None,
    "s5": "outreach_compliance_missing",
    "s6": "crm_data_misuse",
    "s7": "impersonation_signal",
    "s8": "off_scope_behavior",
}


@pytest.mark.parametrize("event", SYNTHETIC_EVENTS, ids=lambda e: e["id"])
def test_parity_holds_event_by_event(pack, reference_pipeline, reference_rules, event):
    """Each synthetic event alone must fail exactly the one intent it is built for."""
    expected_intent = EVENT_EXPECTED_INTENT[event["id"]]
    _, verdicts = _production_verdicts(pack, reference_pipeline, reference_rules, [event])
    failing = {intent for intent, verdict in verdicts.items() if verdict == "fail"}
    assert failing == (set() if expected_intent is None else {expected_intent})


def test_reference_rule_identity_is_preserved_by_the_mapping(reference_rules):
    """Each reference rule maps to the intent carrying its own subject matter.

    Anchored on the reference rule's declared tags rather than on the mapping, so a
    transposition between two intents is caught here even though both would still
    produce a determination.
    """
    expected_tag_by_intent = {
        "unauthorized_spend_without_confirmation": "financial",
        "brand_safety_signal": "brand",
        "outreach_compliance_missing": "outreach",
        "crm_data_misuse": "data_misuse",
        "impersonation_signal": "impersonation",
        "off_scope_behavior": "scope",
    }
    for rule in reference_rules.rules:
        intent_id = GROWTH_RULE_TO_INTENT[rule.id]
        assert expected_tag_by_intent[intent_id] in rule.tags, (
            f"{rule.id} -> {intent_id} does not preserve rule intent"
        )


def test_no_synthetic_content_reaches_the_production_boundary(
    pack, reference_pipeline, reference_rules
):
    working, determinations, baselines = _run_reference(
        reference_pipeline, reference_rules, SYNTHETIC_EVENTS
    )
    evidence = reduce_to_evidence(
        pack,
        evaluated_rule_ids=GROWTH_RULE_TO_INTENT,
        match_counts=count_determinations(determinations),
        baseline_counts=baselines,
    )
    outputs = [
        pack.evaluate(intent_id, entry, commitment_ref=COMMITMENT_REF, source_state="healthy")
        for intent_id, entry in evidence.items()
    ]
    serialised = json.dumps({"evidence": evidence, "outputs": outputs}, sort_keys=True)
    for needle in (
        "get rich", "buyer@example.com", "victim@example.com", "078-05-1120",
        "not a bot", "u/someone", "Buy now.",
    ):
        assert needle not in serialised
    reject_growth_forbidden_keys(evidence)
    for output in outputs:
        reject_growth_forbidden_keys(output)


# --------------------------------------------------------------------------- #
# The reduction boundary
# --------------------------------------------------------------------------- #


def test_a_rule_that_did_not_run_is_indeterminate_not_a_pass(pack):
    """Only the rule that ran gets evidence; the other five stay unobserved."""
    evidence = reduce_to_evidence(
        pack,
        evaluated_rule_ids=["growth.impersonation"],
        match_counts={},
        baseline_counts={"growth.impersonation": 4},
    )
    assert set(evidence) == {"impersonation_signal"}

    # The rule that ran and matched nothing is a clean pass.
    ran = pack.evaluate(
        "impersonation_signal", evidence["impersonation_signal"],
        commitment_ref=COMMITMENT_REF, source_state="healthy",
    )
    assert ran["verdict"] == "pass"

    # Every rule that did not run has no counters at all, so it evaluates to
    # indeterminate rather than inheriting a pass it never earned.
    for intent_id in sorted(EXPECTED_INTENTS - {"impersonation_signal"}):
        assert intent_id not in evidence
        unobserved = pack.evaluate(
            intent_id, {}, commitment_ref=COMMITMENT_REF, source_state="healthy"
        )
        assert unobserved["verdict"] == "indeterminate"


def test_a_rule_that_ran_without_a_baseline_is_indeterminate(pack):
    evidence = reduce_to_evidence(
        pack,
        evaluated_rule_ids=["growth.brand_safety"],
        match_counts={"growth.brand_safety": 0},
        baseline_counts={},
    )
    result = pack.evaluate(
        "brand_safety_signal", evidence["brand_safety_signal"],
        commitment_ref=COMMITMENT_REF, source_state="healthy",
    )
    assert result["verdict"] == "indeterminate"


def test_reduction_rejects_content_and_stray_counters(pack):
    with pytest.raises(GrowthCelError, match="forbidden key"):
        reduce_to_evidence(
            pack, evaluated_rule_ids=GROWTH_RULE_TO_INTENT,
            match_counts={"growth.brand_safety": 1, "flagged_terms": 1}, baseline_counts={},
        )
    with pytest.raises(GrowthCelError, match="did not run"):
        reduce_to_evidence(
            pack, evaluated_rule_ids=["growth.brand_safety"],
            match_counts={"growth.impersonation": 1}, baseline_counts={},
        )
    with pytest.raises(GrowthCelError, match="unknown reference rule ids"):
        reduce_to_evidence(
            pack, evaluated_rule_ids=["growth.not_a_rule"], match_counts={}, baseline_counts={},
        )
    with pytest.raises(GrowthCelError, match="must be a non-negative integer"):
        reduce_to_evidence(
            pack, evaluated_rule_ids=["growth.brand_safety"],
            match_counts={"growth.brand_safety": -1}, baseline_counts={},
        )


def test_count_determinations_reads_only_rule_ids():
    determinations = [
        {"rule_id": "growth.brand_safety", "severity": "high", "title": "off-brand",
         "tags": ["brand"], "evidence_hash": "sha256:" + "a" * 64},
        {"rule_id": "growth.brand_safety", "severity": "high", "title": "off-brand",
         "tags": ["brand"], "evidence_hash": "sha256:" + "b" * 64},
        {"rule_id": "coding.secret_access", "severity": "high", "title": "unrelated lane"},
    ]
    counts = count_determinations(determinations)
    assert counts == {"growth.brand_safety": 2}
    assert json.dumps(counts) == '{"growth.brand_safety": 2}'
