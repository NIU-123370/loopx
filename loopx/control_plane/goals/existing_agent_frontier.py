from __future__ import annotations

from pathlib import Path
from typing import Any

from ...project_prompt import render_cli_command_prefix, shell_arg
from ..todos.active_state_todo_parser import parse_active_state_todos
from ..todos.contract import (
    TODO_STATUS_BLOCKED,
    normalize_todo_claimed_by,
    normalize_todo_status,
)


def existing_runnable_todo_for_agent(
    *,
    project: Path,
    goal: dict[str, Any],
    state_file: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Return one open, unblocked Todo claimed by the selected agent, if any."""
    if not agent_id:
        return None
    state_path = Path(state_file)
    if not state_path.is_absolute():
        state_path = project / state_path
    if not state_path.exists():
        return None
    try:
        state_text = state_path.read_text(encoding="utf-8")
    except OSError:
        return None

    parsed = parse_active_state_todos(state_text, goal=goal, item_limit=None)
    agent_todos = parsed.get("agent_todos")
    if not isinstance(agent_todos, dict):
        return None
    for item in agent_todos.get("items") or []:
        if not isinstance(item, dict) or item.get("done"):
            continue
        if normalize_todo_status(item.get("status")) == TODO_STATUS_BLOCKED:
            continue
        if normalize_todo_claimed_by(item.get("claimed_by")) == agent_id:
            return item
    return None


def build_goal_todo_frontier_steps(
    *,
    command_pack: dict[str, Any],
    commands: dict[str, Any],
    cli_bin: str,
    fine_grained: bool,
) -> list[dict[str, Any]]:
    """Project guided steps that either reuse or create the agent frontier."""
    todo_delta = str(command_pack.get("todo_delta") or "add_new")
    claimed_by = (
        f"--claimed-by {shell_arg(str(command_pack.get('agent_id') or ''))} "
        if command_pack.get("agent_id")
        else "--claimed-by <agent-id> "
    )
    add_new_command_template = (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=command_pack.get('command_runtime_root'))} "
        f"todo add --goal-id "
        f"{shell_arg(str(command_pack.get('goal_id') or ''))} "
        "--project . --role agent "
        f"{claimed_by}"
        "--task-class advancement_task --action-kind <action_kind> "
        "[--target-key <target_key>] --text '<[P0/P1/P2] ...>'"
    )
    if todo_delta == "reuse_existing":
        return [
            {
                "id": "continue_existing_frontier",
                "kind": "conditional_mutation",
                "todo_delta": todo_delta,
                "decision": "reuse_existing",
                "existing_todo_id": command_pack.get("existing_runnable_todo_id"),
                "purpose": (
                    "the resolved agent already owns an open, unblocked "
                    "advancement todo; continue that frontier instead of "
                    "authoring a duplicate todo"
                ),
                "add_new_command_template": add_new_command_template,
                "add_new_escape_hatch": (
                    "bounded fallback only: if the frontier todo is already "
                    "terminal or no longer covers the requested continuation "
                    "when the step executes, author one replacement advancement "
                    "todo with add_new_command_template instead of continuing "
                    "the stale frontier"
                ),
            }
        ]

    return [
        {
            "id": "plan_ranked_todos",
            "kind": "model_checkpoint",
            "prompt": commands.get("goal_start_plan_prompt"),
            "purpose": (
                "produce one public-safe, small, verifiable checkpoint Todo; keep "
                "later options as evidence-linked planning notes"
                if fine_grained
                else "produce concise public-safe P0/P1/P2 todos before todo writeback"
            ),
        },
        {
            "id": "write_ordered_todos",
            "kind": "operator_or_agent_actions",
            "command_template": add_new_command_template,
            "purpose": (
                "write only the current checkpoint Todo; the existing replan path "
                "qualifies any successor after completion evidence"
                if fine_grained
                else "write todos in planner order; capability successors preserve "
                "the admitted action_kind and target_key for later quota re-entry"
            ),
        },
    ]
