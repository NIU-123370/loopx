from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from loopx.control_plane.host_adapter_settlement import (
    HostTodoSettlementRequest,
    host_adapter_turn_instance_id,
)
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo


GOAL_ID = "mcp-settlement-fixture"
AGENT_ID = "claude"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "status: active",
                "updated_at: 2026-08-21T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, state_file


def _run_cli(registry: Path, *args: str) -> tuple[int, dict[str, object]]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(result.stdout)


def _control(registry: Path) -> GoalModeMCPControlPlane:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": GOAL_ID,
            "registry": str(registry),
            "agent_id": AGENT_ID,
        },
    )
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]
    return control


def test_real_mcp_settlement_rejects_missing_writeback_then_commits_same_identity(
    tmp_path: Path,
) -> None:
    registry, state_file = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver the bounded fixture change.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )
    todo_id = str(added["todo_id"])
    request = HostTodoSettlementRequest(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=todo_id,
        runtime_profile="claude_code",
        legacy_host_surface="claude_code",
        scheduler_owner="agent_cli_loop",
        execution_mode="interactive",
        completion_args=(),
    )
    turn_instance_id = host_adapter_turn_instance_id(request)

    guard_rc, guard = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo_id,
        "--turn-instance-id",
        turn_instance_id,
        "--runtime-profile",
        "claude_code",
    )
    assert guard_rc == 0, guard
    assert guard["selected_todo"]["todo_id"] == todo_id  # type: ignore[index]

    premature_rc, premature = _run_cli(
        registry,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo_id,
        "--turn-instance-id",
        turn_instance_id,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
    )
    assert premature_rc == 1
    assert premature["ok"] is False
    assert "accountable refresh-state receipt is missing" in str(
        premature.get("reason") or premature.get("error")
    )

    control = _control(registry)
    output = control.complete_task(
        todo_id,
        AGENT_ID,
        "focused fixture validation passed",
        next_agent_todo="Run the successor fixture check.",
    )
    result = json.loads(output)

    assert result["ok"] is True, json.dumps(result, indent=2, sort_keys=True)
    identity = result["settlement_identity"]
    assert identity["todo_id"] == todo_id
    assert identity["turn_instance_id"] == turn_instance_id
    settlement = result["settlement"]
    assert settlement["durable_writeback"]["ok"] is True
    assert settlement["lifecycle_completion"]["ok"] is True
    assert settlement["quota_spend"]["ok"] is True
    assert settlement["quota_spend"]["appended"] is True

    status_rc, status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "claude_code",
    )
    assert status_rc == 0, status
    assert status["quota"]["spent_slots"] == 1  # type: ignore[index]
    successor_id = status["selected_todo"]["todo_id"]  # type: ignore[index]
    assert successor_id != todo_id
    assert settlement["quota_spend"]["settlement_identity"]["todo_id"] == todo_id

    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    todo_by_id = {
        str(item["todo_id"]): item for item in todos["agent_todos"]["items"]
    }
    assert todo_by_id[todo_id]["status"] == "done"
    assert todo_by_id[str(successor_id)]["status"] == "open"

    replay = json.loads(
        control.complete_task(
            todo_id,
            AGENT_ID,
            "focused fixture validation passed",
            next_agent_todo="Run the successor fixture check.",
        )
    )
    assert replay["ok"] is True, json.dumps(replay, indent=2, sort_keys=True)
    assert replay["settlement_identity"] == identity
    assert replay["settlement"]["quota_spend"]["appended"] is False
    assert replay["settlement"]["quota_spend"]["idempotent_replay"] is True

    replay_status_rc, replay_status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "claude_code",
    )
    assert replay_status_rc == 0, replay_status
    assert replay_status["quota"]["spent_slots"] == 1  # type: ignore[index]


def test_real_mcp_terminal_completion_closes_out_after_spend(
    tmp_path: Path,
) -> None:
    registry, state_file = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Finish the bounded non-delivery fixture.",
        task_class="advancement_task",
        continuation_policy="same_agent_non_delivery",
        claimed_by=AGENT_ID,
    )
    todo_id = str(added["todo_id"])

    result = json.loads(
        _control(registry).complete_task(
            todo_id,
            AGENT_ID,
            "terminal fixture validation passed",
            no_follow_up=True,
        )
    )

    assert result["ok"] is True, json.dumps(result, indent=2, sort_keys=True)
    identity = result["settlement_identity"]
    assert identity["todo_id"] == todo_id
    settlement = result["settlement"]
    assert settlement["lifecycle_completion"]["completion_continuation"] == (
        "active_goal"
    )
    assert settlement["quota_spend"]["appended"] is True
    terminal = settlement["terminal_closeout"]
    assert terminal["completion_continuation"] == "no_followup"
    assert terminal["completion_recovery"] == "same_turn_terminal_closeout"
    assert [
        receipt["step_kind"]
        for receipt in terminal["settlement_result"]["receipts"]
    ] == [
        "validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]

    status_rc, status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "claude_code",
    )
    assert status_rc == 0, status
    assert status["quota"]["spent_slots"] == 1  # type: ignore[index]

    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    completed = next(
        item
        for item in todos["agent_todos"]["items"]
        if item["todo_id"] == todo_id
    )
    assert completed["status"] == "done"
    assert "no_followup=true" in state_file.read_text(encoding="utf-8")
