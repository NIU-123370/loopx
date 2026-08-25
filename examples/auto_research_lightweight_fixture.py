from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "loopx-auto-research-demo"
METRIC_NAME = "fixture_quality_score"
BASELINE = 1.0
HYPOTHESIS_ID = "hyp_state_a2a_round"
TODO_ID = "todo_auto_research_demo_001"
AGENT_ID = "research-executor"
MECHANISM_FAMILY = "state_a2a_iteration"
HYPOTHESIS_TEXT = "Use a small state-mediated handoff loop to improve the shared candidate."
GROUNDING_REF = "fixture:lane_authored_metric"


def research_contract(*, goal_id: str = GOAL_ID) -> dict[str, Any]:
    return {
        "schema_version": "research_contract_v0",
        "goal_id": goal_id,
        "research_objective": "Validate compact multi-agent research evidence.",
        "editable_scope": ["candidate_strategy", "hypothesis_text", "todo_handoff"],
        "protected_scope": ["metric_definition", "baseline_metric", "holdout_split"],
        "metric": {
            "name": METRIC_NAME,
            "direction": "maximize",
            "baseline": BASELINE,
        },
        "dev_eval": "public fixture evaluator on dev split",
        "holdout_eval": "public fixture evaluator on holdout split",
        "promotion_policy": "dev_and_holdout_improved",
    }


def eval_result(split: str, *, value: float | None | object = None) -> dict[str, Any]:
    if value is None and split in {"dev", "holdout"}:
        value = 4.0 if split == "dev" else 4.5
    return {
        "schema_version": "auto_research_lightweight_eval_result_v0",
        "split": split,
        "metric": {
            "name": METRIC_NAME,
            "direction": "maximize",
            "value": value,
            "baseline": BASELINE,
        },
        "eval_status": "scored",
        "primary_metric_status": "improved",
        "artifact_refs": [f"public_metric:{split}:state_a2a_round"],
        "protected_scope_clean": True,
        "no_upload": True,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_registry(
    temp: Path,
    *,
    registered_agents: list[str],
) -> Path:
    runtime_root = temp / "runtime"
    project = temp / "project"
    project.mkdir()
    registry = temp / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "adapter": {
                            "kind": "fixture",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": registered_agents,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry


def run_auto_research_cli(
    registry: Path,
    *args: str,
    expect_ok: bool = True,
) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            "auto-research",
            *args,
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = json.loads(result.stdout)
    if expect_ok:
        assert result.returncode == 0 and payload["ok"] is True, (result, payload)
    else:
        assert result.returncode != 0 and payload["ok"] is False, (result, payload)
    return payload


def write_contract_and_results(temp: Path) -> tuple[Path, Path, Path]:
    contract = temp / "research-contract.public.json"
    dev = temp / "dev-result.public.json"
    holdout = temp / "holdout-result.public.json"
    write_json(contract, research_contract())
    write_json(dev, eval_result("dev"))
    write_json(holdout, eval_result("holdout"))
    return contract, dev, holdout
