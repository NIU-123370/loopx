from __future__ import annotations

from typing import Any

from ..runtime.local_state_write_correctness import (
    build_todo_write_correctness_dry_run_packet,
)
from .contract import normalize_todo_claimed_by, normalize_todo_id


def attach_todo_write_correctness_dry_run_packet(
    payload: dict[str, Any],
    *,
    goal_id: str,
    write_class: str,
    state_text: str,
) -> dict[str, Any]:
    if not payload.get("dry_run"):
        return payload
    changed = any(
        payload.get(field)
        for field in (
            "changed",
            "added",
            "metadata_updated",
            "completed",
            "superseded",
        )
    )
    payload["local_state_write_correctness"] = (
        build_todo_write_correctness_dry_run_packet(
            goal_id=goal_id,
            write_class=write_class,
            state_text=state_text,
            todo_id=normalize_todo_id(payload.get("todo_id")),
            role=str(payload.get("role") or ""),
            section=str(payload.get("section") or ""),
            claimed_by=normalize_todo_claimed_by(payload.get("claimed_by")),
            changed=changed,
        )
    )
    return payload
