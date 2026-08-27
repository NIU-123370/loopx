from __future__ import annotations

from copy import deepcopy
from typing import Any

from demo.auto_research.artifact_receipt import (
    AUTO_RESEARCH_ARTIFACT_RECEIPT_SCHEMA_VERSION,
    build_auto_research_artifact_receipt,
)
from demo.auto_research.delivery_contract import (
    AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION,
    normalize_auto_research_delivery_contract,
)
from demo.auto_research.evidence_packet import (
    build_auto_research_evidence_packet,
    build_auto_research_rollout_events,
)
from demo.auto_research.research_state import (
    build_research_evidence_graph_from_rollout_events,
)
from demo.auto_research.terminal_result_contract import (
    build_peer_review_event,
    build_terminal_decision_event,
)


GOAL_ID = "wish-artifact-fixture"
PRODUCER = "research-executor"
PROMOTER = "evaluator-promoter"
REVIEWER = "independent-reviewer"
REGISTERED_AGENTS = [PRODUCER, PROMOTER, REVIEWER]


def _research_contract() -> dict[str, Any]:
    return {
        "schema_version": "research_contract_v0",
        "goal_id": GOAL_ID,
        "research_objective": (
            "Test whether a reusable skill improves held-out tool use."
        ),
        "editable_scope": ["candidate_skill", "experiment_driver"],
        "protected_scope": ["heldout_tasks", "metric_definition"],
        "metric": {
            "name": "heldout_success_rate",
            "direction": "maximize",
            "baseline": 0.5,
        },
        "dev_eval": "run public dev evaluator",
        "holdout_eval": "run public holdout evaluator",
        "promotion_policy": "dev_and_holdout_improved",
    }


def _delivery_contract(
    *,
    include_report: bool = True,
    reentry_conditions: list[str] | None = None,
) -> dict[str, Any]:
    artifacts = [
        {
            "artifact_ref": "artifact:experiment",
            "kind": "experiment",
            "description": "Runnable comparison experiment.",
            "required": True,
        },
    ]
    required_refs = ["artifact:experiment"]
    if include_report:
        artifacts.append(
            {
                "artifact_ref": "artifact:report",
                "kind": "report",
                "description": "Evidence-backed result and limitation report.",
                "required": True,
            }
        )
        required_refs.append("artifact:report")
    return {
        "schema_version": AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION,
        "contract_ref": "contract:skill-library-generalization",
        "wish": {
            "original_text": (
                "Verify whether a durable skill library improves general tool "
                "use instead of memorizing one repository."
            ),
            "intent_summary": (
                "Compare a fixed base agent with and without the skill library "
                "on held-out repositories."
            ),
            "assumptions": ["A public held-out task split is available."],
            "non_goals": ["Do not train or deploy a production model."],
        },
        "research_contract": _research_contract(),
        "required_artifacts": artifacts,
        "acceptance_criteria": [
            {
                "criterion_id": "heldout_generalization",
                "description": (
                    "Held-out performance is terminally decided from clean "
                    "evidence and an independent review."
                ),
                "hypothesis_id": "hyp_generalization",
                "required_artifact_refs": required_refs,
                "requires_independent_review": True,
                "required": True,
            }
        ],
        "failure_policy": {
            "fallback_artifact_refs": ["artifact:report"]
            if include_report
            else [],
            "reentry_conditions": reentry_conditions
            or ["Provide a new held-out repository split."],
        },
    }


def _eval_result(
    *,
    split: str,
    value: float,
    metric_status: str,
    artifact_refs: list[str],
    failure_kind: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "auto_research_lightweight_eval_result_v0",
        "split": split,
        "metric": {
            "name": "heldout_success_rate",
            "direction": "maximize",
            "value": value,
            "baseline": 0.5,
        },
        "eval_status": "scored",
        "primary_metric_status": metric_status,
        "failure_kind": failure_kind,
        "artifact_refs": artifact_refs,
        "protected_scope_clean": True,
        "no_upload": True,
    }


def _packet(
    delivery_contract: dict[str, Any],
    *,
    supported: bool,
    include_report: bool = True,
) -> dict[str, Any]:
    refs = ["artifact:experiment"]
    if include_report:
        refs.append("artifact:report")
    return build_auto_research_evidence_packet(
        contract=delivery_contract,
        eval_results=[
            _eval_result(
                split="dev",
                value=0.7 if supported else 0.4,
                metric_status="improved" if supported else "regressed",
                artifact_refs=refs,
                failure_kind=None if supported else "mechanism_contradicted",
            ),
            _eval_result(
                split="holdout",
                value=0.65 if supported else 0.35,
                metric_status="improved" if supported else "regressed",
                artifact_refs=refs,
                failure_kind=None if supported else "mechanism_contradicted",
            ),
        ],
        hypothesis_id="hyp_generalization",
        todo_id="todo_generalization",
        agent_id=PRODUCER,
        claimed_by=PRODUCER,
        mechanism_family="skill_library_generalization",
        hypothesis="The skill library improves held-out tool use.",
    )


def _terminal_events(
    packet: dict[str, Any],
    *,
    supported: bool,
) -> list[dict[str, Any]]:
    events = build_auto_research_rollout_events(
        packet,
        recorded_at="2026-08-25T00:00:00Z",
    )
    graph = build_research_evidence_graph_from_rollout_events(
        goal_id=GOAL_ID,
        rollout_events=events,
    )
    decision = build_terminal_decision_event(
        evidence_graph=graph,
        hypothesis_id="hyp_generalization",
        outcome="promoted" if supported else "retired",
        reason="holdout_validated" if supported else "contradicted",
        decided_by=PROMOTER,
        evidence_refs=["decision:generalization"],
        recorded_at="2026-08-25T00:01:00Z",
    )
    review = build_peer_review_event(
        evidence_graph=graph,
        rollout_events=[*events, decision],
        hypothesis_id="hyp_generalization",
        reviewer_agent_id=REVIEWER,
        verdict="approve",
        evidence_refs=["review:independent"],
        require_independent=True,
        registered_agent_ids=REGISTERED_AGENTS,
        recorded_at="2026-08-25T00:02:00Z",
    )
    return [*events, decision, review]


def _tampered_evidence_lineage(
    events: list[dict[str, Any]],
    *,
    field: str,
    value: str | None,
) -> list[dict[str, Any]]:
    tampered = deepcopy(events)
    for event in tampered:
        if event["event_kind"] != "research_evidence":
            continue
        if value is None:
            event["details"].pop(field, None)
        else:
            event["details"][field] = value
    return tampered


def _assert_lineage_tampering_fails_closed(
    events: list[dict[str, Any]],
) -> None:
    graph = build_research_evidence_graph_from_rollout_events(
        goal_id=GOAL_ID,
        rollout_events=events,
    )
    assert graph["evidence_event_count"] == 0
    assert all(not node["artifact_refs"] for node in graph["nodes"])

    receipt = build_auto_research_artifact_receipt(
        delivery_contract=_delivery_contract(),
        rollout_events=events,
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] != "verified"
    assert receipt["acceptance_results"][0]["status"] != "verified"
    assert "artifact:experiment" in receipt["artifacts"][
        "missing_required_refs"
    ]


def test_delivery_contract_normalization_is_stable() -> None:
    normalized = normalize_auto_research_delivery_contract(_delivery_contract())
    assert normalize_auto_research_delivery_contract(normalized) == normalized
    assert normalized["wish"]["wish_id"].startswith("wish_")
    assert normalized["contract_revision"].startswith("sha256:")


def test_legacy_research_contract_keeps_existing_event_shape() -> None:
    packet = build_auto_research_evidence_packet(
        contract=_research_contract(),
        eval_results=[
            _eval_result(
                split="dev",
                value=0.7,
                metric_status="improved",
                artifact_refs=["artifact:experiment"],
            )
        ],
        hypothesis_id="hyp_generalization",
        todo_id="todo_generalization",
        agent_id=PRODUCER,
        claimed_by=PRODUCER,
        mechanism_family="skill_library_generalization",
        hypothesis="The skill library improves held-out tool use.",
    )
    events = build_auto_research_rollout_events(packet)
    assert "delivery_contract" not in packet
    assert [event["event_kind"] for event in events] == [
        "research_hypothesis",
        "research_evidence",
    ]
    assert all(
        "contract_revision" not in event.get("details", {})
        for event in events
    )


def test_verified_artifact_receipt_closes_the_delivery_contract() -> None:
    contract = _delivery_contract()
    packet = _packet(contract, supported=True)
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=_terminal_events(packet, supported=True),
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["schema_version"] == AUTO_RESEARCH_ARTIFACT_RECEIPT_SCHEMA_VERSION
    assert receipt["status"] == "verified"
    assert receipt["failure_feedback"] is None
    assert receipt["contract"]["state"] == "current"
    assert receipt["artifacts"]["missing_required_refs"] == []
    assert receipt["acceptance_results"][0]["status"] == "verified"
    assert receipt["learning_disposition"] == "candidate"
    assert receipt["boundary"]["user_acceptance_inferred"] is False


def test_terminally_unfulfilled_wish_returns_actionable_feedback() -> None:
    contract = _delivery_contract()
    packet = _packet(contract, supported=False)
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=_terminal_events(packet, supported=False),
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] == "not_fulfilled"
    feedback = receipt["failure_feedback"]
    assert feedback["failure_kinds"] == ["mechanism_contradicted"]
    assert feedback["unmet_criteria"] == ["heldout_generalization"]
    assert feedback["verified_boundary"] == []
    assert feedback["fallback_artifact_refs"] == ["artifact:report"]
    assert feedback["reentry_conditions"] == [
        "Provide a new held-out repository split.",
        "resolve:heldout_generalization:mechanism_contradicted",
    ]
    assert receipt["learning_disposition"] == "candidate"


def test_retired_result_without_required_review_remains_inconclusive() -> None:
    contract = _delivery_contract()
    packet = _packet(contract, supported=False)
    events = build_auto_research_rollout_events(packet)
    graph = build_research_evidence_graph_from_rollout_events(
        goal_id=GOAL_ID,
        rollout_events=events,
    )
    decision = build_terminal_decision_event(
        evidence_graph=graph,
        hypothesis_id="hyp_generalization",
        outcome="retired",
        reason="contradicted",
        decided_by=PROMOTER,
    )
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=[*events, decision],
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] == "inconclusive"
    assert receipt["failure_feedback"]["failure_kinds"] == [
        "independent_review_missing"
    ]


def test_missing_artifact_is_partial_not_verified() -> None:
    contract = _delivery_contract()
    packet = _packet(contract, supported=True, include_report=False)
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=_terminal_events(packet, supported=True),
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] == "partial"
    assert receipt["artifacts"]["missing_required_refs"] == ["artifact:report"]
    assert receipt["failure_feedback"]["failure_kinds"] == [
        "required_artifact_missing"
    ]
    assert receipt["failure_feedback"]["missing_required_artifact_refs"] == [
        "artifact:report"
    ]


def test_contract_change_invalidates_prior_terminal_result() -> None:
    old_contract = _delivery_contract()
    old_packet = _packet(old_contract, supported=True)
    events = _terminal_events(old_packet, supported=True)
    changed_contract = _delivery_contract(
        reentry_conditions=[
            "Provide two new held-out repository families.",
        ]
    )
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=changed_contract,
        rollout_events=events,
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] == "stale"
    assert receipt["contract"]["state"] == "stale"
    assert receipt["failure_feedback"]["failure_kinds"] == [
        "contract_revision_changed",
        "required_artifact_missing",
    ]
    assert (
        receipt["failure_feedback"]["reentry_conditions"][0]
        == "Provide two new held-out repository families."
    )


def test_latest_contract_revision_replaces_prior_hypothesis_evidence() -> None:
    old_contract = _delivery_contract()
    changed_contract = _delivery_contract(
        reentry_conditions=["Provide two new held-out repository families."]
    )
    old_events = build_auto_research_rollout_events(
        _packet(old_contract, supported=True)
    )
    new_events = build_auto_research_rollout_events(
        _packet(changed_contract, supported=False)
    )
    graph = build_research_evidence_graph_from_rollout_events(
        goal_id=GOAL_ID,
        rollout_events=[*old_events, *new_events],
    )
    node = graph["nodes"][0]
    assert graph["evidence_event_count"] == 2
    assert node["status"] == "contradicted"
    assert node["contract_revision"] != old_events[0]["details"][
        "contract_revision"
    ]


def test_tampered_evidence_wish_id_cannot_verify_same_revision() -> None:
    contract = _delivery_contract()
    events = _terminal_events(_packet(contract, supported=True), supported=True)
    _assert_lineage_tampering_fails_closed(
        _tampered_evidence_lineage(
            events,
            field="wish_id",
            value="wish_tampered",
        )
    )


def test_tampered_evidence_contract_ref_cannot_verify_same_revision() -> None:
    contract = _delivery_contract()
    events = _terminal_events(_packet(contract, supported=True), supported=True)
    _assert_lineage_tampering_fails_closed(
        _tampered_evidence_lineage(
            events,
            field="contract_ref",
            value="contract:tampered",
        )
    )


def test_partial_evidence_lineage_cannot_verify_same_revision() -> None:
    contract = _delivery_contract()
    events = _terminal_events(_packet(contract, supported=True), supported=True)
    _assert_lineage_tampering_fails_closed(
        _tampered_evidence_lineage(
            events,
            field="contract_ref",
            value=None,
        )
    )


def test_partial_hypothesis_lineage_cannot_verify_or_raise_key_error() -> None:
    contract = _delivery_contract()
    events = deepcopy(
        _terminal_events(_packet(contract, supported=True), supported=True)
    )
    for event in events:
        if event["event_kind"] != "research_hypothesis":
            continue
        event["details"].pop("contract_ref")
        event["details"].pop("contract_revision")
    _assert_lineage_tampering_fails_closed(events)


def test_missing_terminal_decision_is_inconclusive() -> None:
    contract = _delivery_contract()
    packet = _packet(contract, supported=True)
    events = build_auto_research_rollout_events(packet)
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=events,
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] == "inconclusive"
    assert receipt["failure_feedback"]["failure_kinds"] == [
        "terminal_decision_missing"
    ]


def test_missing_contract_record_returns_reentry_feedback() -> None:
    contract = _delivery_contract()
    packet = _packet(contract, supported=True)
    events = [
        event
        for event in _terminal_events(packet, supported=True)
        if event["classification"] != AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION
    ]
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=events,
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] == "inconclusive"
    assert "contract_record_missing" in receipt["failure_feedback"][
        "failure_kinds"
    ]
    assert receipt["failure_feedback"]["reentry_conditions"][0] == (
        "Provide a new held-out repository split."
    )
    assert "record_current_delivery_contract" in receipt["failure_feedback"][
        "reentry_conditions"
    ]


def test_independent_review_rejection_is_inconclusive_feedback() -> None:
    contract = _delivery_contract()
    packet = _packet(contract, supported=True)
    events = build_auto_research_rollout_events(packet)
    graph = build_research_evidence_graph_from_rollout_events(
        goal_id=GOAL_ID,
        rollout_events=events,
    )
    decision = build_terminal_decision_event(
        evidence_graph=graph,
        hypothesis_id="hyp_generalization",
        outcome="promoted",
        reason="holdout_validated",
        decided_by=PROMOTER,
    )
    review = build_peer_review_event(
        evidence_graph=graph,
        rollout_events=[*events, decision],
        hypothesis_id="hyp_generalization",
        reviewer_agent_id=REVIEWER,
        verdict="reject",
        require_independent=True,
        registered_agent_ids=REGISTERED_AGENTS,
    )
    receipt = build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=[*events, decision, review],
        registered_agent_ids=REGISTERED_AGENTS,
    )
    assert receipt["status"] == "inconclusive"
    assert receipt["failure_feedback"]["failure_kinds"] == [
        "independent_review_rejected"
    ]


def test_delivery_contract_rejects_undeclared_artifact_reference() -> None:
    contract = _delivery_contract()
    contract["acceptance_criteria"][0]["required_artifact_refs"].append(
        "artifact:undeclared"
    )
    try:
        normalize_auto_research_delivery_contract(contract)
    except ValueError as exc:
        assert "undeclared artifacts" in str(exc)
    else:
        raise AssertionError("undeclared artifact references must fail closed")
