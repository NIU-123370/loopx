from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .delivery_contract import (
    AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION,
    AutoResearchContractLineage,
    auto_research_contract_lineage_matches,
    normalize_auto_research_contract_lineage,
    normalize_auto_research_delivery_contract,
)
from .research_state import build_research_evidence_graph_from_rollout_events
from .terminal_result_query import build_terminal_result_query
from loopx.agent_registry import registered_agent_ids_from_registry
from loopx.history import load_registry
from loopx.paths import resolve_runtime_root
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path


AUTO_RESEARCH_ARTIFACT_RECEIPT_SCHEMA_VERSION = (
    "auto_research_artifact_receipt_v0"
)
ARTIFACT_RECEIPT_STATUSES = {
    "verified",
    "partial",
    "inconclusive",
    "not_fulfilled",
    "stale",
}


def _details(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("details")
    return value if isinstance(value, Mapping) else {}


def _contract_events(
    rollout_events: Sequence[Mapping[str, Any]],
    *,
    wish_id: str,
) -> list[Mapping[str, Any]]:
    return [
        event
        for event in rollout_events
        if str(event.get("classification") or "")
        == AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION
        and str(_details(event).get("wish_id") or "") == wish_id
    ]


def _lineage_events(
    rollout_events: Sequence[Mapping[str, Any]],
    *,
    hypothesis_id: str,
) -> list[Mapping[str, Any]]:
    return [
        event
        for event in rollout_events
        if str(_details(event).get("hypothesis_id") or "") == hypothesis_id
        and str(event.get("event_kind") or "")
        in {"research_hypothesis", "research_evidence"}
    ]


def _artifact_refs(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_lineage: AutoResearchContractLineage,
) -> list[str]:
    refs = {
        str(ref)
        for event in events
        if auto_research_contract_lineage_matches(
            _details(event),
            expected=expected_lineage,
        )
        for ref in event.get("artifact_refs") or []
        if str(ref).strip()
    }
    return sorted(refs)


def _failure_kind(result: Mapping[str, Any]) -> str:
    if result.get("decision_state") == "conflict":
        return "conflicting_terminal_decisions"
    review = result.get("peer_review")
    if isinstance(review, Mapping):
        review_failure = _review_failure_kind(review)
        if review_failure:
            return review_failure
    decision = result.get("terminal_decision")
    if (
        isinstance(decision, Mapping)
        and decision.get("outcome") == "retired"
        and decision.get("reason")
    ):
        return str(decision["reason"])
    if result.get("research_status") == "needs_retry":
        return "research_retry_pending"
    return "terminal_decision_missing"


def _review_failure_kind(review: Mapping[str, Any]) -> str | None:
    state = str(review.get("state") or "")
    verdict = (
        str(review.get("verdict") or "")
        if state == "independent_reviewed"
        else None
    )
    return {
        ("conflict", None): "conflicting_peer_reviews",
        ("independent_reviewed", "needs_more_evidence"): "needs_more_evidence",
        ("independent_reviewed", "reject"): "independent_review_rejected",
        ("self_review_only", None): "independent_review_missing",
        ("unreviewed", None): "independent_review_missing",
    }.get((state, verdict))


def _result_parts(
    result: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(result, Mapping):
        return {}, {}
    decision = result.get("terminal_decision")
    review = result.get("peer_review")
    return (
        decision if isinstance(decision, Mapping) else {},
        review if isinstance(review, Mapping) else {},
    )


def _lineage_state(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_lineage: AutoResearchContractLineage,
) -> tuple[list[str], list[Mapping[str, Any]]]:
    observed_revisions = sorted(
        {
            str(_details(event).get("contract_revision") or "")
            for event in events
            if _details(event).get("contract_revision")
        }
    )
    current = [
        event
        for event in events
        if auto_research_contract_lineage_matches(
            _details(event),
            expected=expected_lineage,
        )
    ]
    return observed_revisions, current


def _result_artifact_refs(
    result: Mapping[str, Any] | None,
    *,
    decision: Mapping[str, Any],
    review: Mapping[str, Any],
) -> list[str]:
    if not isinstance(result, Mapping) or result.get("decision_state") != "current":
        return []
    refs = list(decision.get("evidence_refs") or [])
    refs.extend(
        str(ref)
        for item in review.get("reviews") or []
        if isinstance(item, Mapping)
        for ref in item.get("evidence_refs") or []
    )
    return refs


def _has_complete_research_lineage(
    events: Sequence[Mapping[str, Any]],
) -> bool:
    event_kinds = {str(event.get("event_kind") or "") for event in events}
    return {"research_hypothesis", "research_evidence"} <= event_kinds


def _criterion_artifact_state(
    criterion: Mapping[str, Any],
    *,
    current_lineage: Sequence[Mapping[str, Any]],
    expected_lineage: AutoResearchContractLineage,
    result: Mapping[str, Any] | None,
    decision: Mapping[str, Any],
    review: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    if not _has_complete_research_lineage(current_lineage):
        return [], sorted(criterion["required_artifact_refs"])
    observed = sorted(
        set(
            _artifact_refs(
                current_lineage,
                expected_lineage=expected_lineage,
            )
        )
        | set(
            _result_artifact_refs(
                result,
                decision=decision,
                review=review,
            )
        )
    )
    missing = sorted(set(criterion["required_artifact_refs"]) - set(observed))
    return observed, missing


def _criterion_status(
    criterion: Mapping[str, Any],
    *,
    result: Mapping[str, Any] | None,
    decision: Mapping[str, Any],
    review: Mapping[str, Any],
    current_lineage: Sequence[Mapping[str, Any]],
    observed_revisions: Sequence[str],
    missing_artifacts: Sequence[str],
) -> str:
    if observed_revisions and not current_lineage:
        return "stale"
    if not _has_complete_research_lineage(current_lineage) or result is None:
        return "inconclusive"
    if result.get("decision_state") == "stale":
        return "stale"
    if result.get("decision_state") == "conflict":
        return "inconclusive"
    independent_approved = (
        review.get("state") == "independent_reviewed"
        and review.get("verdict") == "approve"
    )
    review_satisfied = (
        not criterion["requires_independent_review"] or independent_approved
    )
    if decision.get("outcome") == "retired":
        return "not_fulfilled" if review_satisfied else "inconclusive"
    if decision.get("outcome") != "promoted" or not review_satisfied:
        return "inconclusive"
    return "partial" if missing_artifacts else "verified"


def _criterion_failure_kind(
    *,
    status: str,
    criterion: Mapping[str, Any],
    result: Mapping[str, Any] | None,
    evidence_node: Mapping[str, Any] | None,
    review: Mapping[str, Any],
    current_lineage: Sequence[Mapping[str, Any]],
) -> str:
    if status == "stale":
        return "contract_revision_changed"
    if status == "partial":
        return "required_artifact_missing"
    if (
        status == "inconclusive"
        and not _has_complete_research_lineage(current_lineage)
    ):
        return "contract_evidence_missing"
    review_failure = _review_failure_kind(review)
    if isinstance(result, Mapping) and _result_has_review_failure(
        criterion,
        result=result,
        review=review,
        review_failure=review_failure,
    ):
        return _failure_kind(result)
    if (
        status != "verified"
        and isinstance(evidence_node, Mapping)
        and evidence_node.get("failure_kind")
    ):
        return str(evidence_node["failure_kind"])
    if status != "verified" and isinstance(result, Mapping):
        return _failure_kind(result)
    return ""


def _result_has_review_failure(
    criterion: Mapping[str, Any],
    *,
    result: Mapping[str, Any],
    review: Mapping[str, Any],
    review_failure: str | None,
) -> bool:
    return (
        result.get("decision_state") == "conflict"
        or review.get("state") == "conflict"
        or (
            criterion["requires_independent_review"]
            and review_failure == "independent_review_missing"
        )
        or review_failure
        in {"needs_more_evidence", "independent_review_rejected"}
    )


def _criterion_result(
    criterion: Mapping[str, Any],
    *,
    result: Mapping[str, Any] | None,
    evidence_node: Mapping[str, Any] | None,
    lineage_events: Sequence[Mapping[str, Any]],
    expected_lineage: AutoResearchContractLineage,
) -> dict[str, Any]:
    observed_revisions, current_lineage = _lineage_state(
        lineage_events,
        expected_lineage=expected_lineage,
    )
    decision, review = _result_parts(result)
    observed_artifacts, missing_artifacts = _criterion_artifact_state(
        criterion,
        current_lineage=current_lineage,
        expected_lineage=expected_lineage,
        result=result,
        decision=decision,
        review=review,
    )

    status = _criterion_status(
        criterion,
        result=result,
        decision=decision,
        review=review,
        current_lineage=current_lineage,
        observed_revisions=observed_revisions,
        missing_artifacts=missing_artifacts,
    )
    failure_kind = _criterion_failure_kind(
        status=status,
        criterion=criterion,
        result=result,
        evidence_node=evidence_node,
        review=review,
        current_lineage=current_lineage,
    )
    return {
        "criterion_id": criterion["criterion_id"],
        "description": criterion["description"],
        "hypothesis_id": criterion["hypothesis_id"],
        "required": criterion["required"],
        "status": status,
        "required_artifact_refs": list(criterion["required_artifact_refs"]),
        "observed_artifact_refs": observed_artifacts,
        "missing_artifact_refs": missing_artifacts,
        "decision_state": _decision_state(result),
        "terminal_outcome": str(decision.get("outcome") or ""),
        "terminal_reason": str(decision.get("reason") or ""),
        "review_state": str(review.get("state") or "not_applicable"),
        "review_verdict": str(review.get("verdict") or ""),
        "failure_kind": failure_kind,
        "measurement_scope": _measurement_scope(evidence_node),
        "observed_contract_revisions": observed_revisions,
    }


def _decision_state(result: Mapping[str, Any] | None) -> str:
    if not isinstance(result, Mapping):
        return "missing"
    return str(result.get("decision_state") or "missing")


def _measurement_scope(evidence_node: Mapping[str, Any] | None) -> str:
    if not isinstance(evidence_node, Mapping):
        return ""
    return str(evidence_node.get("measurement_scope") or "")


def _receipt_status(
    criteria: Sequence[Mapping[str, Any]],
    *,
    missing_required_artifacts: Sequence[str],
    contract_state: str,
) -> str:
    required = [item for item in criteria if item.get("required") is True]
    statuses = {str(item.get("status") or "") for item in required}
    if contract_state == "stale" or "stale" in statuses:
        return "stale"
    if contract_state != "current":
        return "inconclusive"
    if "not_fulfilled" in statuses:
        return "not_fulfilled"
    if required and statuses == {"verified"} and not missing_required_artifacts:
        return "verified"
    if "verified" in statuses or "partial" in statuses:
        return "partial"
    return "inconclusive"


def _failure_feedback(
    *,
    status: str,
    contract_state: str,
    criteria: Sequence[Mapping[str, Any]],
    missing_required_artifacts: Sequence[str],
    fallback_artifact_refs: Sequence[str],
    reentry_conditions: Sequence[str],
    observed_artifact_refs: Sequence[str],
) -> dict[str, Any] | None:
    if status == "verified":
        return None
    failed = [
        item
        for item in criteria
        if item.get("required") is True and item.get("status") != "verified"
    ]
    verified = [
        item
        for item in criteria
        if item.get("status") == "verified"
    ]
    fallbacks = sorted(set(fallback_artifact_refs) & set(observed_artifact_refs))
    failure_kinds, derived_reentry = _failure_reentry_details(
        failed,
        contract_state=contract_state,
        missing_required_artifacts=missing_required_artifacts,
    )
    summaries = {
        "stale": (
            "The delivery contract changed after the recorded research evidence; "
            "rerun the affected criteria against the current contract."
        ),
        "not_fulfilled": (
            "The current evidence supports a terminal conclusion that at least "
            "one required criterion cannot be fulfilled under this contract."
        ),
        "partial": (
            "Some required criteria or artifacts are verified, but the complete "
            "delivery contract is not satisfied."
        ),
        "inconclusive": (
            "The available evidence is insufficient for a verified or terminal "
            "not-fulfilled conclusion."
        ),
    }
    return {
        "summary": summaries[status],
        "failure_kinds": sorted(failure_kinds),
        "unmet_criteria": [str(item["criterion_id"]) for item in failed],
        "verified_boundary": [str(item["criterion_id"]) for item in verified],
        "missing_required_artifact_refs": list(missing_required_artifacts),
        "fallback_artifact_refs": fallbacks,
        "reentry_conditions": list(
            dict.fromkeys([*reentry_conditions, *derived_reentry])
        ),
    }


def _failure_reentry_details(
    failed: Sequence[Mapping[str, Any]],
    *,
    contract_state: str,
    missing_required_artifacts: Sequence[str],
) -> tuple[set[str], list[str]]:
    failure_kinds = {
        str(item.get("failure_kind") or "")
        for item in failed
        if item.get("failure_kind")
    }
    reentry = [
        f"resolve:{item['criterion_id']}:{item['failure_kind']}"
        for item in failed
    ]
    if contract_state == "missing":
        failure_kinds.add("contract_record_missing")
        reentry.insert(0, "record_current_delivery_contract")
    if missing_required_artifacts:
        failure_kinds.add("required_artifact_missing")
        reentry.extend(
            f"provide:{artifact_ref}"
            for artifact_ref in missing_required_artifacts
        )
    return failure_kinds, reentry


def _contract_state(
    contract_events: Sequence[Mapping[str, Any]],
    *,
    expected_lineage: AutoResearchContractLineage,
) -> tuple[str | None, str]:
    latest_revision = None
    if contract_events:
        latest_revision = (
            str(_details(contract_events[-1]).get("contract_revision") or "")
            or None
        )
    if latest_revision is None:
        return None, "missing"
    if contract_events and auto_research_contract_lineage_matches(
        _details(contract_events[-1]),
        expected=expected_lineage,
    ):
        return latest_revision, "current"
    return latest_revision, "stale"


def _indexed_records(
    records: object,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(records, Sequence):
        return {}
    return {
        str(item.get("hypothesis_id") or ""): item
        for item in records
        if isinstance(item, Mapping)
    }


def _acceptance_results(
    *,
    contract: Mapping[str, Any],
    rollout_events: Sequence[Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
    evidence_nodes: Mapping[str, Mapping[str, Any]],
    expected_lineage: AutoResearchContractLineage,
) -> list[dict[str, Any]]:
    return [
        _criterion_result(
            criterion,
            result=results.get(criterion["hypothesis_id"]),
            evidence_node=evidence_nodes.get(criterion["hypothesis_id"]),
            lineage_events=_lineage_events(
                rollout_events,
                hypothesis_id=criterion["hypothesis_id"],
            ),
            expected_lineage=expected_lineage,
        )
        for criterion in contract["acceptance_criteria"]
    ]


def _artifact_summary(
    contract: Mapping[str, Any],
    criteria: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    observed = sorted(
        {
            str(ref)
            for criterion in criteria
            for ref in criterion["observed_artifact_refs"]
        }
    )
    missing = sorted(
        {
            str(artifact["artifact_ref"])
            for artifact in contract["required_artifacts"]
            if artifact["required"]
        }
        - set(observed)
    )
    return observed, missing


def _verification_summary(
    *,
    criteria: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    evidence_graph_revision: str,
) -> dict[str, Any]:
    return {
        "required_count": sum(item["required"] for item in criteria),
        "verified_count": sum(
            item["status"] == "verified" for item in criteria
        ),
        "not_fulfilled_count": sum(
            item["status"] == "not_fulfilled" for item in criteria
        ),
        "independent_review_required": any(
            item["requires_independent_review"]
            for item in contract["acceptance_criteria"]
        ),
        "evidence_graph_revision": evidence_graph_revision,
    }


def build_auto_research_artifact_receipt(
    *,
    delivery_contract: Mapping[str, Any],
    rollout_events: Sequence[Mapping[str, Any]],
    registered_agent_ids: Sequence[str],
) -> dict[str, Any]:
    contract = normalize_auto_research_delivery_contract(delivery_contract)
    research_contract = contract["research_contract"]
    expected_lineage = normalize_auto_research_contract_lineage(
        {
            "wish_id": contract["wish"]["wish_id"],
            "contract_ref": contract["contract_ref"],
            "contract_revision": contract["contract_revision"],
        },
        field="delivery_contract",
        required=True,
    )
    contract_events = _contract_events(
        rollout_events,
        wish_id=contract["wish"]["wish_id"],
    )
    latest_revision, contract_state = _contract_state(
        contract_events,
        expected_lineage=expected_lineage,
    )
    evidence_graph = build_research_evidence_graph_from_rollout_events(
        goal_id=research_contract["goal_id"],
        rollout_events=[dict(event) for event in rollout_events],
    )
    query = build_terminal_result_query(
        evidence_graph=evidence_graph,
        rollout_events=rollout_events,
        registered_agent_ids=registered_agent_ids,
        include_history=True,
    )
    criteria = _acceptance_results(
        contract=contract,
        rollout_events=rollout_events,
        results=_indexed_records(query["results"]),
        evidence_nodes=_indexed_records(evidence_graph.get("nodes")),
        expected_lineage=expected_lineage,
    )
    observed_artifact_refs, missing_required_artifacts = _artifact_summary(
        contract,
        criteria,
    )
    status = _receipt_status(
        criteria,
        missing_required_artifacts=missing_required_artifacts,
        contract_state=contract_state,
    )
    return {
        "ok": True,
        "schema_version": AUTO_RESEARCH_ARTIFACT_RECEIPT_SCHEMA_VERSION,
        "goal_id": research_contract["goal_id"],
        "wish": contract["wish"],
        "contract": {
            "contract_ref": contract["contract_ref"],
            "contract_revision": contract["contract_revision"],
            "latest_recorded_revision": latest_revision,
            "state": contract_state,
        },
        "status": status,
        "artifacts": {
            "required": contract["required_artifacts"],
            "observed_refs": observed_artifact_refs,
            "missing_required_refs": missing_required_artifacts,
        },
        "acceptance_results": criteria,
        "verification_summary": _verification_summary(
            criteria=criteria,
            contract=contract,
            evidence_graph_revision=query["evidence_graph_revision"],
        ),
        "failure_feedback": _failure_feedback(
            status=status,
            contract_state=contract_state,
            criteria=criteria,
            missing_required_artifacts=missing_required_artifacts,
            fallback_artifact_refs=contract["failure_policy"][
                "fallback_artifact_refs"
            ],
            reentry_conditions=contract["failure_policy"]["reentry_conditions"],
            observed_artifact_refs=observed_artifact_refs,
        ),
        "learning_disposition": (
            "candidate" if status in {"verified", "not_fulfilled"} else "none"
        ),
        "boundary": {
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
            "user_acceptance_inferred": False,
            "automatic_skill_promotion": False,
        },
    }


def load_auto_research_artifact_receipt(
    *,
    contract_path: str | Path,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> dict[str, Any]:
    raw_contract = json.loads(
        Path(contract_path).expanduser().read_text(encoding="utf-8")
    )
    if not isinstance(raw_contract, Mapping):
        raise ValueError("delivery contract file must contain a JSON object")
    contract = normalize_auto_research_delivery_contract(raw_contract)
    goal_id = contract["research_contract"]["goal_id"]
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    events = load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    return build_auto_research_artifact_receipt(
        delivery_contract=contract,
        rollout_events=events,
        registered_agent_ids=registered_agent_ids_from_registry(
            registry_path,
            goal_id,
        ),
    )
