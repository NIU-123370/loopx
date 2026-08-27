from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from .evidence_packet import (
    _compact_public_text,
    _compact_public_text_list,
    _compact_public_token,
    validate_research_contract,
)


AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION = (
    "auto_research_delivery_contract_v0"
)
AUTO_RESEARCH_CONTRACT_LINEAGE_SCHEMA_VERSION = (
    "auto_research_contract_lineage_v0"
)

MAX_ASSUMPTIONS = 8
MAX_NON_GOALS = 8
MAX_REQUIRED_ARTIFACTS = 12
MAX_ACCEPTANCE_CRITERIA = 12
MAX_REENTRY_CONDITIONS = 8

_DELIVERY_CONTRACT_FIELDS = {
    "schema_version",
    "contract_ref",
    "contract_revision",
    "wish",
    "research_contract",
    "required_artifacts",
    "acceptance_criteria",
    "failure_policy",
}
_WISH_FIELDS = {
    "wish_id",
    "original_text",
    "intent_summary",
    "assumptions",
    "non_goals",
}
_ARTIFACT_FIELDS = {"artifact_ref", "kind", "description", "required"}
_CRITERION_FIELDS = {
    "criterion_id",
    "description",
    "hypothesis_id",
    "required_artifact_refs",
    "requires_independent_review",
    "required",
}
_FAILURE_POLICY_FIELDS = {"fallback_artifact_refs", "reentry_conditions"}
AUTO_RESEARCH_CONTRACT_LINEAGE_FIELDS = (
    "wish_id",
    "contract_ref",
    "contract_revision",
)


class AutoResearchContractLineage(NamedTuple):
    wish_id: str
    contract_ref: str
    contract_revision: str


class AutoResearchContractLineageError(ValueError):
    pass


def normalize_auto_research_contract_lineage(
    value: Mapping[str, Any],
    *,
    field: str,
    required: bool = False,
) -> AutoResearchContractLineage | None:
    supplied = {
        name
        for name in AUTO_RESEARCH_CONTRACT_LINEAGE_FIELDS
        if str(value.get(name) or "").strip()
    }
    if not supplied:
        if required:
            raise AutoResearchContractLineageError(
                f"{field} requires complete contract lineage"
            )
        return None
    if supplied != set(AUTO_RESEARCH_CONTRACT_LINEAGE_FIELDS):
        raise AutoResearchContractLineageError(
            f"{field} contract lineage must provide wish_id, contract_ref, "
            "and contract_revision together"
        )
    try:
        return AutoResearchContractLineage(
            *(
                _compact_public_text(
                    value.get(name),
                    field=f"{field}.{name}",
                    max_len=180,
                )
                for name in AUTO_RESEARCH_CONTRACT_LINEAGE_FIELDS
            )
        )
    except ValueError as exc:
        raise AutoResearchContractLineageError(str(exc)) from exc


def auto_research_contract_lineage_matches(
    value: Mapping[str, Any],
    *,
    expected: AutoResearchContractLineage | None,
) -> bool:
    try:
        return (
            normalize_auto_research_contract_lineage(
                value,
                field="contract_lineage",
            )
            == expected
        )
    except ValueError:
        return False


def auto_research_contract_lineage_dict(
    lineage: AutoResearchContractLineage,
) -> dict[str, str]:
    return dict(zip(AUTO_RESEARCH_CONTRACT_LINEAGE_FIELDS, lineage))


def _strict_mapping(
    value: object,
    *,
    field: str,
    allowed: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(
            f"{field} contains unsupported fields: {', '.join(unexpected)}"
        )
    return value


def _bounded_list(
    value: object,
    *,
    field: str,
    maximum: int,
) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a list")
    if len(value) > maximum:
        raise ValueError(f"{field} must contain at most {maximum} items")
    return value


def _boolean(value: object, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _wish_id(*, goal_id: str, original_text: str) -> str:
    encoded = f"{goal_id}\n{original_text}".encode("utf-8")
    return f"wish_{hashlib.sha256(encoded).hexdigest()[:16]}"


def _normalize_wish(
    value: object,
    *,
    goal_id: str,
) -> dict[str, Any]:
    wish = _strict_mapping(
        value,
        field="delivery_contract.wish",
        allowed=_WISH_FIELDS,
    )
    original_text = _compact_public_text(
        wish.get("original_text"),
        field="delivery_contract.wish.original_text",
        max_len=500,
    )
    derived_wish_id = _wish_id(
        goal_id=goal_id,
        original_text=original_text,
    )
    provided_wish_id = wish.get("wish_id")
    if provided_wish_id and provided_wish_id != derived_wish_id:
        raise ValueError("delivery_contract.wish.wish_id does not match the wish")
    return {
        "wish_id": derived_wish_id,
        "original_text": original_text,
        "intent_summary": _compact_public_text(
            wish.get("intent_summary"),
            field="delivery_contract.wish.intent_summary",
            max_len=300,
        ),
        "assumptions": _compact_public_text_list(
            _bounded_list(
                wish.get("assumptions") or [],
                field="delivery_contract.wish.assumptions",
                maximum=MAX_ASSUMPTIONS,
            ),
            field="delivery_contract.wish.assumptions",
        ),
        "non_goals": _compact_public_text_list(
            _bounded_list(
                wish.get("non_goals") or [],
                field="delivery_contract.wish.non_goals",
                maximum=MAX_NON_GOALS,
            ),
            field="delivery_contract.wish.non_goals",
        ),
    }


def _normalize_required_artifacts(value: object) -> list[dict[str, Any]]:
    required_artifacts: list[dict[str, Any]] = []
    seen_artifact_refs: set[str] = set()
    for index, item in enumerate(
        _bounded_list(
            value,
            field="delivery_contract.required_artifacts",
            maximum=MAX_REQUIRED_ARTIFACTS,
        )
    ):
        artifact = _strict_mapping(
            item,
            field=f"delivery_contract.required_artifacts[{index}]",
            allowed=_ARTIFACT_FIELDS,
        )
        artifact_ref = _compact_public_text(
            artifact.get("artifact_ref"),
            field=f"delivery_contract.required_artifacts[{index}].artifact_ref",
            max_len=180,
        )
        if artifact_ref in seen_artifact_refs:
            raise ValueError(
                "delivery_contract.required_artifacts artifact_ref values "
                "must be unique"
            )
        seen_artifact_refs.add(artifact_ref)
        required_artifacts.append(
            {
                "artifact_ref": artifact_ref,
                "kind": _compact_public_token(
                    artifact.get("kind"),
                    field=f"delivery_contract.required_artifacts[{index}].kind",
                ),
                "description": _compact_public_text(
                    artifact.get("description"),
                    field=(
                        "delivery_contract.required_artifacts"
                        f"[{index}].description"
                    ),
                    max_len=300,
                ),
                "required": _boolean(
                    artifact.get("required"),
                    field=(
                        "delivery_contract.required_artifacts"
                        f"[{index}].required"
                    ),
                    default=True,
                ),
            }
        )
    if not required_artifacts:
        raise ValueError("delivery_contract requires at least one artifact")
    if not any(item["required"] for item in required_artifacts):
        raise ValueError(
            "delivery_contract requires at least one required artifact"
        )
    return required_artifacts


def _normalize_acceptance_criteria(
    value: object,
    *,
    artifact_refs: set[str],
) -> list[dict[str, Any]]:
    acceptance_criteria: list[dict[str, Any]] = []
    seen_criterion_ids: set[str] = set()
    for index, item in enumerate(
        _bounded_list(
            value,
            field="delivery_contract.acceptance_criteria",
            maximum=MAX_ACCEPTANCE_CRITERIA,
        )
    ):
        criterion = _strict_mapping(
            item,
            field=f"delivery_contract.acceptance_criteria[{index}]",
            allowed=_CRITERION_FIELDS,
        )
        criterion_id = _compact_public_token(
            criterion.get("criterion_id"),
            field=f"delivery_contract.acceptance_criteria[{index}].criterion_id",
        )
        if criterion_id in seen_criterion_ids:
            raise ValueError(
                "delivery_contract.acceptance_criteria criterion_id values "
                "must be unique"
            )
        seen_criterion_ids.add(criterion_id)
        required_refs = _compact_public_text_list(
            _bounded_list(
                criterion.get("required_artifact_refs") or [],
                field=(
                    "delivery_contract.acceptance_criteria"
                    f"[{index}].required_artifact_refs"
                ),
                maximum=MAX_REQUIRED_ARTIFACTS,
            ),
            field=(
                "delivery_contract.acceptance_criteria"
                f"[{index}].required_artifact_refs"
            ),
        )
        unknown_refs = sorted(set(required_refs) - artifact_refs)
        if unknown_refs:
            raise ValueError(
                "acceptance criteria reference undeclared artifacts: "
                + ", ".join(unknown_refs)
            )
        if len(set(required_refs)) != len(required_refs):
            raise ValueError(
                "acceptance criteria required_artifact_refs must be unique"
            )
        acceptance_criteria.append(
            {
                "criterion_id": criterion_id,
                "description": _compact_public_text(
                    criterion.get("description"),
                    field=(
                        "delivery_contract.acceptance_criteria"
                        f"[{index}].description"
                    ),
                    max_len=300,
                ),
                "hypothesis_id": _compact_public_token(
                    criterion.get("hypothesis_id"),
                    field=(
                        "delivery_contract.acceptance_criteria"
                        f"[{index}].hypothesis_id"
                    ),
                ),
                "required_artifact_refs": required_refs,
                "requires_independent_review": _boolean(
                    criterion.get("requires_independent_review"),
                    field=(
                        "delivery_contract.acceptance_criteria"
                        f"[{index}].requires_independent_review"
                    ),
                    default=True,
                ),
                "required": _boolean(
                    criterion.get("required"),
                    field=(
                        "delivery_contract.acceptance_criteria"
                        f"[{index}].required"
                    ),
                    default=True,
                ),
            }
        )
    if not acceptance_criteria:
        raise ValueError(
            "delivery_contract requires at least one acceptance criterion"
        )
    if not any(item["required"] for item in acceptance_criteria):
        raise ValueError(
            "delivery_contract requires at least one required acceptance criterion"
        )
    return acceptance_criteria


def _normalize_failure_policy(
    value: object,
    *,
    artifact_refs: set[str],
) -> dict[str, list[str]]:
    failure_policy = _strict_mapping(
        value or {},
        field="delivery_contract.failure_policy",
        allowed=_FAILURE_POLICY_FIELDS,
    )
    fallback_artifact_refs = _compact_public_text_list(
        _bounded_list(
            failure_policy.get("fallback_artifact_refs") or [],
            field="delivery_contract.failure_policy.fallback_artifact_refs",
            maximum=MAX_REQUIRED_ARTIFACTS,
        ),
        field="delivery_contract.failure_policy.fallback_artifact_refs",
    )
    unknown_fallbacks = sorted(set(fallback_artifact_refs) - artifact_refs)
    if unknown_fallbacks:
        raise ValueError(
            "failure policy references undeclared artifacts: "
            + ", ".join(unknown_fallbacks)
        )
    return {
        "fallback_artifact_refs": fallback_artifact_refs,
        "reentry_conditions": _compact_public_text_list(
            _bounded_list(
                failure_policy.get("reentry_conditions") or [],
                field="delivery_contract.failure_policy.reentry_conditions",
                maximum=MAX_REENTRY_CONDITIONS,
            ),
            field="delivery_contract.failure_policy.reentry_conditions",
        ),
    }


def normalize_auto_research_delivery_contract(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    raw = _strict_mapping(
        value,
        field="delivery_contract",
        allowed=_DELIVERY_CONTRACT_FIELDS,
    )
    if raw.get("schema_version") != AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION:
        raise ValueError(
            "delivery_contract.schema_version must be "
            f"{AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION}"
        )
    research_contract = validate_research_contract(
        dict(
            _strict_mapping(
                raw.get("research_contract"),
                field="delivery_contract.research_contract",
                allowed={
                    "schema_version",
                    "goal_id",
                    "research_objective",
                    "editable_scope",
                    "protected_scope",
                    "metric",
                    "dev_eval",
                    "holdout_eval",
                    "promotion_policy",
                },
            )
        )
    )
    required_artifacts = _normalize_required_artifacts(
        raw.get("required_artifacts")
    )
    artifact_refs = {
        str(artifact["artifact_ref"]) for artifact in required_artifacts
    }
    normalized = {
        "schema_version": AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION,
        "contract_ref": _compact_public_text(
            raw.get("contract_ref"),
            field="delivery_contract.contract_ref",
            max_len=160,
        ),
        "wish": _normalize_wish(
            raw.get("wish"),
            goal_id=research_contract["goal_id"],
        ),
        "research_contract": research_contract,
        "required_artifacts": required_artifacts,
        "acceptance_criteria": _normalize_acceptance_criteria(
            raw.get("acceptance_criteria"),
            artifact_refs=artifact_refs,
        ),
        "failure_policy": _normalize_failure_policy(
            raw.get("failure_policy"),
            artifact_refs=artifact_refs,
        ),
    }
    contract_revision = _canonical_digest(normalized)
    provided_revision = raw.get("contract_revision")
    if provided_revision and provided_revision != contract_revision:
        raise ValueError(
            "delivery_contract.contract_revision does not match normalized content"
        )
    normalized["contract_revision"] = contract_revision
    return normalized


def build_auto_research_contract_lineage(
    research_contract: Mapping[str, Any],
    *,
    delivery_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if delivery_contract:
        normalized_delivery = normalize_auto_research_delivery_contract(
            delivery_contract
        )
        return {
            "schema_version": AUTO_RESEARCH_CONTRACT_LINEAGE_SCHEMA_VERSION,
            "contract_revision": normalized_delivery["contract_revision"],
            "contract_ref": normalized_delivery["contract_ref"],
            "wish_id": normalized_delivery["wish"]["wish_id"],
            "delivery_contract_schema_version": (
                AUTO_RESEARCH_DELIVERY_CONTRACT_SCHEMA_VERSION
            ),
        }
    return {
        "schema_version": AUTO_RESEARCH_CONTRACT_LINEAGE_SCHEMA_VERSION,
        "contract_revision": _canonical_digest(dict(research_contract)),
        "contract_ref": None,
        "wish_id": None,
        "delivery_contract_schema_version": None,
    }
