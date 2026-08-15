from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...materials import find_registry_goal, goal_repo
from ..runtime.validation_command import (
    CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
    run_caller_validation,
)
from .active_state_editing import find_todo_block
from .contract import TODO_STATUS_DONE

# Kept safely under the 30s outer CLI/MCP subprocess budget so a timed-out
# validation still produces a typed receipt before the outer call is killed.
_COMPLETION_VALIDATION_TIMEOUT_SECONDS = 20
# Per-todo overrides (declared on `todo add`) must also stay under that outer
# budget for the same reason; the writer-side range check enforces this.
COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS = 29


def _resolve_goal_repo_workspace(registry_path: Path, goal_id: str) -> Path | None:
    """Resolve the goal's repository directory to use as the validation workspace."""
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if goal is None:
        return None
    repo = goal_repo(goal)
    if repo is None or not repo.is_dir():
        return None
    return repo


def _read_declared_validation(
    *, state_file: Path, todo_id: str, role: str | None
) -> tuple[str | None, str | None, int | None, bool]:
    """Pre-read a todo's declared validation command without the mutation lock.

    Returns ``(validation_command, validation_label, validation_timeout_seconds,
    already_completed)`` from the markdown state file, or ``(None, None, None,
    False)`` when the todo is not materialized in markdown. Read-only; safe to
    call before acquiring the state-file lock so a slow validation command does
    not block concurrent todo operations on the same goal (the MUTATION lock
    deadline is 5s). ``validation_command`` and ``validation_timeout_seconds``
    are set only at ``todo add`` and have no update path, so the values cannot
    drift between this pre-read and the in-lock commit. A stored timeout that
    fails to parse as an int falls back to ``None`` (the default), matching the
    writer-side range check that guarantees a well-formed value.
    """
    try:
        lines = state_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None, None, None, False
    match = find_todo_block(lines, todo_id=todo_id, role=role)
    if not match:
        return None, None, None, False
    _role, _section, _start, _end, block = match
    try:
        timeout_seconds: int | None = (
            int(block["validation_timeout_seconds"])
            if block.get("validation_timeout_seconds")
            else None
        )
    except (TypeError, ValueError):
        timeout_seconds = None
    return (
        block.get("validation_command") or None,
        block.get("validation_label") or None,
        timeout_seconds,
        block.get("status") == TODO_STATUS_DONE,
    )


def _run_declared_completion_validation(
    *,
    validation_command: str | None,
    validation_label: str | None,
    validation_timeout_seconds: int | None,
    registry_path: Path,
    goal_id: str,
) -> dict[str, Any] | None:
    """Run a todo's declared caller-approved validation command.

    Returns ``None`` when no ``validation_command`` is declared (the unchanged
    fast path). ``validation_timeout_seconds`` overrides the module default
    when declared on ``todo add``; ``None`` keeps the default. Otherwise always
    returns a privacy-safe receipt whose ``passed`` is True only when the
    command ran and exited zero; setup failures (no repository workspace),
    timeouts, missing executables, and malformed commands are all reported as
    ``passed=False`` receipts rather than raised, so completion can surface a
    typed failure without committing.
    """
    if not validation_command:
        return None
    timeout_seconds = (
        validation_timeout_seconds
        if validation_timeout_seconds is not None
        else _COMPLETION_VALIDATION_TIMEOUT_SECONDS
    )
    label = validation_label or "todo completion validation"
    workspace = _resolve_goal_repo_workspace(registry_path, goal_id)
    if workspace is None:
        return {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "workspace_unavailable",
            "summary": (
                "validation_command is declared but the goal has no "
                "repository workspace to run it in"
            ),
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }
    try:
        return run_caller_validation(
            workspace,
            validation_command=str(validation_command),
            validation_label=label,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "timeout",
            "summary": (
                f"validation command timed out after {timeout_seconds}s"
            ),
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }
    except (FileNotFoundError, PermissionError) as exc:
        return {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "command_not_run",
            "summary": f"validation command could not be launched: {exc}",
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }
    except ValueError as exc:
        # shlex.split rejects malformed (e.g. unbalanced-quote) commands.
        return {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "command_malformed",
            "summary": f"validation command could not be parsed: {exc}",
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }


def run_completion_validation_gate(
    *,
    state_file: Path,
    todo_id: str,
    role: str | None,
    registry_path: Path,
    goal_id: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    """Run the caller-approved completion validation gate, OUTSIDE the mutation lock.

    Returns a ``validation_blocked_completion`` failure payload when a declared
    validation command does not pass (the caller returns it unchanged so the
    durable writeback and quota spend are both skipped), or ``None`` when there
    is no declared command, the command passes, or the completion is a dry_run
    or a terminal replay (no gate). Read-only w.r.t. the state file; safe to
    call before acquiring the mutation lock so a multi-second validation command
    does not block concurrent todo operations on the same goal.
    """
    (
        validation_command,
        validation_label,
        validation_timeout_seconds,
        already_completed,
    ) = _read_declared_validation(state_file=state_file, todo_id=todo_id, role=role)
    completion_validation = (
        _run_declared_completion_validation(
            validation_command=validation_command,
            validation_label=validation_label,
            validation_timeout_seconds=validation_timeout_seconds,
            registry_path=registry_path,
            goal_id=goal_id,
        )
        if not dry_run and not already_completed
        else None
    )
    if completion_validation is None or completion_validation.get("passed") is True:
        return None
    return {
        "ok": False,
        "dry_run": dry_run,
        "completed": False,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "changed": False,
        "validation": completion_validation,
        "validation_blocked_completion": True,
    }
