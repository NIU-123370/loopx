from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

import loopx.todos as todos_module
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo, complete_goal_todo

GOAL_ID = "todo-completion-validation"
AGENT = "codex-author"

_PASS_COMMAND = f'{shlex.quote(sys.executable)} -c "raise SystemExit(0)"'
_FAIL_COMMAND = f'{shlex.quote(sys.executable)} -c "raise SystemExit(1)"'
_SLEEP_COMMAND = f'{shlex.quote(sys.executable)} -c "import time; time.sleep(30)"'


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-08-12T00:00:00+00:00",
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
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, state


def _agent_todo(state: Path, todo_id: str) -> dict:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    return next(
        item
        for item in todos["agent_todos"]["items"]
        if item["todo_id"] == todo_id
    )


def _add_todo(
    registry: Path,
    *,
    validation_command: str | None = None,
    validation_label: str | None = None,
) -> dict:
    return add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver one bounded change.",
        task_class="advancement_task",
        claimed_by=AGENT,
        validation_command=validation_command,
        validation_label=validation_label,
    )


def test_validation_command_declared_and_passing_commits_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command=_PASS_COMMAND,
        validation_label="caller-declared smoke",
    )
    # Spy on the executor so the test fails if the gate is silently skipped.
    original_runner = todos_module.run_caller_validation
    calls = {"count": 0}

    def counting_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        return original_runner(*args, **kwargs)

    monkeypatch.setattr(todos_module, "run_caller_validation", counting_runner)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="validated completion",
    )
    assert calls["count"] == 1  # the gate actually ran the declared command
    assert result["ok"] is True
    assert result["changed"] is True
    assert "validation_blocked_completion" not in result
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_missing_validation_executable_returns_typed_receipt(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry, validation_command="nonexistent-binary-xyz-12345")
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "command_not_run"
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_malformed_validation_command_returns_typed_receipt(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    # Unbalanced quote -> shlex.split raises ValueError -> typed receipt.
    todo = _add_todo(registry, validation_command="echo 'unbalanced")
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "command_malformed"
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_validation_command_declared_and_failing_blocks_completion(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command=_FAIL_COMMAND,
        validation_label="caller-declared smoke",
    )
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    # Completion is blocked: nothing committed, evidence stays only a claim.
    assert result["ok"] is False
    assert result["completed"] is False
    assert result["changed"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["exit_code"] == 1
    assert receipt["command_label"] == "caller-declared smoke"
    # Privacy invariant preserved.
    assert receipt["stdout_captured"] is False
    assert receipt["stderr_captured"] is False
    assert receipt["local_path_captured"] is False
    # State is unchanged: the todo is still open.
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_no_validation_command_keeps_fast_path_unchanged(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry)  # no validation_command declared
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="plain completion",
    )
    assert result["ok"] is True
    assert result["changed"] is True
    assert "validation_blocked_completion" not in result
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_validation_timeout_blocks_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        todos_module, "_COMPLETION_VALIDATION_TIMEOUT_SECONDS", 0.5
    )
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry, validation_command=_SLEEP_COMMAND)
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "timeout"
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_terminal_replay_short_circuits_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry, validation_command=_PASS_COMMAND)

    original_runner = todos_module.run_caller_validation
    calls = {"count": 0}

    def counting_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        return original_runner(*args, **kwargs)

    monkeypatch.setattr(todos_module, "run_caller_validation", counting_runner)

    first = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="validated completion",
    )
    assert first["ok"] is True
    assert calls["count"] == 1  # validation ran once on the real completion

    replay = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="duplicate completion",
    )
    # Replay short-circuits before the validation gate; the command is not re-run.
    assert calls["count"] == 1
    assert replay["ok"] is True
