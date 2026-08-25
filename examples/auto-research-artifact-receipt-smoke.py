#!/usr/bin/env python3
"""Exercise the Auto Research Wish-to-Artifact receipt lifecycle."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.auto_research_lightweight_fixture import (  # noqa: E402
    AGENT_ID,
    GOAL_ID,
    HYPOTHESIS_ID,
    HYPOTHESIS_TEXT,
    MECHANISM_FAMILY,
    TODO_ID,
    eval_result,
    research_contract,
    run_auto_research_cli,
    write_json,
    write_registry,
)


PROMOTER = "evaluator-promoter"
REVIEWER = "independent-reviewer"
EXPERIMENT_REF = "artifact:state-a2a-experiment"
REPORT_REF = "artifact:state-a2a-report"


def delivery_contract() -> dict[str, Any]:
    return {
        "schema_version": "auto_research_delivery_contract_v0",
        "contract_ref": "contract:state-a2a-evidence",
        "wish": {
            "original_text": (
                "Verify whether state-mediated handoffs improve the shared "
                "research candidate."
            ),
            "intent_summary": (
                "Run a bounded dev and holdout comparison and deliver the "
                "experiment plus a result report."
            ),
            "assumptions": [
                "The public fixture metric represents useful progress."
            ],
            "non_goals": [
                "Do not publish or deploy the experimental candidate."
            ],
        },
        "research_contract": research_contract(),
        "required_artifacts": [
            {
                "artifact_ref": EXPERIMENT_REF,
                "kind": "experiment",
                "description": "Runnable state-mediated comparison.",
                "required": True,
            },
            {
                "artifact_ref": REPORT_REF,
                "kind": "report",
                "description": "Evidence-backed result and limitation report.",
                "required": True,
            },
        ],
        "acceptance_criteria": [
            {
                "criterion_id": "heldout_result",
                "description": (
                    "The candidate has a terminal held-out decision with "
                    "independent review."
                ),
                "hypothesis_id": HYPOTHESIS_ID,
                "required_artifact_refs": [EXPERIMENT_REF, REPORT_REF],
                "requires_independent_review": True,
                "required": True,
            }
        ],
        "failure_policy": {
            "fallback_artifact_refs": [REPORT_REF],
            "reentry_conditions": [
                "Provide a new public evaluator or revised held-out task set."
            ],
        },
    }

def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        registry = write_registry(
            temp,
            registered_agents=[AGENT_ID, PROMOTER, REVIEWER],
        )
        contract_path = temp / "delivery-contract.public.json"
        dev_path = temp / "dev-result.public.json"
        holdout_path = temp / "holdout-result.public.json"
        write_json(contract_path, delivery_contract())
        dev = eval_result("dev")
        holdout = eval_result("holdout")
        dev["artifact_refs"].extend([EXPERIMENT_REF, REPORT_REF])
        holdout["artifact_refs"].extend([EXPERIMENT_REF, REPORT_REF])
        write_json(dev_path, dev)
        write_json(holdout_path, holdout)

        packet = run_auto_research_cli(
            registry,
            "evidence",
            "--contract",
            str(contract_path),
            "--eval-result",
            str(dev_path),
            "--eval-result",
            str(holdout_path),
            "--hypothesis-id",
            HYPOTHESIS_ID,
            "--todo-id",
            TODO_ID,
            "--agent-id",
            AGENT_ID,
            "--claimed-by",
            AGENT_ID,
            "--mechanism-family",
            MECHANISM_FAMILY,
            "--hypothesis",
            HYPOTHESIS_TEXT,
        )
        assert packet["delivery_contract"]["wish"]["wish_id"].startswith("wish_")
        assert packet["contract_lineage"]["contract_revision"].startswith(
            "sha256:"
        )
        packet_path = temp / "evidence-packet.public.json"
        write_json(packet_path, packet)
        first_append = run_auto_research_cli(
            registry,
            "append-evidence",
            "--packet",
            str(packet_path),
        )
        second_append = run_auto_research_cli(
            registry,
            "append-evidence",
            "--packet",
            str(packet_path),
        )
        assert first_append["counts_by_kind"]["validation"] == 1, first_append
        assert second_append["appended_count"] == 0, second_append

        run_auto_research_cli(
            registry,
            "decide",
            "--goal-id",
            GOAL_ID,
            "--hypothesis-id",
            HYPOTHESIS_ID,
            "--outcome",
            "promoted",
            "--reason",
            "holdout_validated",
            "--agent-id",
            PROMOTER,
            "--evidence-ref",
            "decision:heldout",
            "--execute",
        )
        run_auto_research_cli(
            registry,
            "review",
            "--goal-id",
            GOAL_ID,
            "--hypothesis-id",
            HYPOTHESIS_ID,
            "--reviewer-agent-id",
            REVIEWER,
            "--verdict",
            "approve",
            "--require-independent",
            "--evidence-ref",
            "review:independent",
            "--execute",
        )
        receipt = run_auto_research_cli(
            registry,
            "artifact-receipt",
            "--contract",
            str(contract_path),
        )
        assert receipt["status"] == "verified", receipt
        assert receipt["failure_feedback"] is None, receipt
        assert receipt["artifacts"]["missing_required_refs"] == [], receipt
        assert receipt["acceptance_results"][0]["status"] == "verified", receipt
        assert receipt["boundary"]["automatic_skill_promotion"] is False, receipt

    print("auto-research-artifact-receipt-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
