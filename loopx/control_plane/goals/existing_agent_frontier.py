from __future__ import annotations

from pathlib import Path
from typing import Any

from ..todos.active_state_todo_parser import parse_active_state_todos
from ..todos.contract import normalize_todo_claimed_by


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

    parsed = parse_active_state_todos(state_text, goal=goal)
    agent_todos = parsed.get("agent_todos")
    if not isinstance(agent_todos, dict):
        return None
    for item in agent_todos.get("items") or []:
        if not isinstance(item, dict) or item.get("done") or item.get("blocking"):
            continue
        if normalize_todo_claimed_by(item.get("claimed_by")) == agent_id:
            return item
    return None
