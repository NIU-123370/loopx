"""Provider-neutral Todo settlement orchestration for shipping host adapters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from .effect_program import SettlementIdentity
from .todos.contract import normalize_todo_id


HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION = "host_adapter_todo_settlement_v0"


class HostGuardState(StrEnum):
    SELECTED = "selected"
    TERMINAL_NO_SELECTION = "terminal_no_selection"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class HostGuardSelection:
    state: HostGuardState
    todo_id: str | None = None
    reason: str | None = None
    settlement_identity: SettlementIdentity | None = None


@dataclass(frozen=True, slots=True)
class HostTodoSettlementRequest:
    goal_id: str
    agent_id: str
    todo_id: str
    runtime_profile: str
    legacy_host_surface: str
    scheduler_owner: str
    execution_mode: str
    completion_args: tuple[str, ...]
    no_follow_up: bool = False


class HostCliRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        legacy_args: list[str] | None = None,
    ) -> str: ...


def host_adapter_turn_instance_id(request: HostTodoSettlementRequest) -> str:
    """Return a retry-stable public-safe identity for one Todo completion."""

    seed = "\0".join((request.goal_id, request.agent_id, request.todo_id))
    return "mcp-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def classify_host_guard_snapshot(value: str) -> HostGuardSelection:
    """Classify a guard without collapsing terminal and invalid snapshots."""

    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return HostGuardSelection(
            HostGuardState.INVALID,
            reason="quota guard returned malformed JSON",
        )
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        reason = (
            str(payload.get("error") or payload.get("reason") or "")
            if isinstance(payload, Mapping)
            else ""
        )
        return HostGuardSelection(
            HostGuardState.INVALID,
            reason=reason or "quota guard did not return an ok object",
        )
    heartbeat_receipt = payload.get("heartbeat_receipt")
    if isinstance(heartbeat_receipt, Mapping):
        raw_identity = heartbeat_receipt.get("settlement_identity")
        if isinstance(raw_identity, Mapping):
            goal_id = str(raw_identity.get("goal_id") or "").strip()
            agent_id = str(raw_identity.get("agent_id") or "").strip()
            todo_id = normalize_todo_id(raw_identity.get("todo_id"))
            turn_instance_id = str(
                raw_identity.get("turn_instance_id") or ""
            ).strip()
            if not (goal_id and agent_id and todo_id and turn_instance_id):
                return HostGuardSelection(
                    HostGuardState.INVALID,
                    reason="quota guard returned an incomplete settlement identity",
                )
            identity = SettlementIdentity(
                goal_id=goal_id,
                agent_id=agent_id,
                todo_id=todo_id,
                turn_instance_id=turn_instance_id,
            )
            if raw_identity.get("effect_id") != identity.effect_id:
                return HostGuardSelection(
                    HostGuardState.INVALID,
                    reason="quota guard returned a mismatched settlement effect id",
                )
            return HostGuardSelection(
                HostGuardState.SELECTED,
                todo_id=todo_id,
                settlement_identity=identity,
            )
    selected = payload.get("selected_todo")
    if isinstance(selected, Mapping):
        todo_id = normalize_todo_id(selected.get("todo_id"))
        if todo_id:
            return HostGuardSelection(HostGuardState.SELECTED, todo_id=todo_id)
        return HostGuardSelection(
            HostGuardState.INVALID,
            reason="quota guard selected_todo has no typed todo_id",
        )
    if (
        payload.get("should_run") is False
        and payload.get("effective_action") == "terminal_no_followup"
    ):
        return HostGuardSelection(HostGuardState.TERMINAL_NO_SELECTION)
    return HostGuardSelection(
        HostGuardState.INVALID,
        reason="quota guard has no authoritative Todo selection",
    )


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _blocked_payload(
    request: HostTodoSettlementRequest,
    *,
    stage: str,
    reason: str,
    guard_state: HostGuardState | None = None,
    completion: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "schema_version": HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION,
        "ok": False,
        "completed": bool(completion and completion.get("completed") is True),
        "goal_id": request.goal_id,
        "todo_id": request.todo_id,
        "settlement_blocked_completion": True,
        "settlement": {
            "ok": False,
            "failed_stage": stage,
            "reason": reason,
        },
    }
    if guard_state is not None:
        payload["settlement"]["guard_state"] = guard_state.value
    if completion is not None:
        payload["completion"] = dict(completion)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _identity_matches(
    payload: Mapping[str, Any],
    expected: SettlementIdentity,
) -> bool:
    identity = payload.get("settlement_identity")
    if not isinstance(identity, Mapping):
        return False
    return (
        identity.get("goal_id") == expected.goal_id
        and identity.get("agent_id") == expected.agent_id
        and identity.get("todo_id") == expected.todo_id
        and identity.get("turn_instance_id") == expected.turn_instance_id
        and identity.get("effect_id") == expected.effect_id
    )


def settle_host_todo_completion(
    request: HostTodoSettlementRequest,
    *,
    run_cli: HostCliRunner,
) -> str:
    """Guard, complete, write back, and spend one Todo under one identity."""

    turn_instance_id = host_adapter_turn_instance_id(request)
    expected_identity = SettlementIdentity(
        goal_id=request.goal_id,
        agent_id=request.agent_id,
        todo_id=request.todo_id,
        turn_instance_id=turn_instance_id,
    )
    guard_common = [
        "quota",
        "should-run",
        "--goal-id",
        request.goal_id,
        "--agent-id",
        request.agent_id,
        "--todo-id",
        request.todo_id,
        "--turn-instance-id",
        turn_instance_id,
    ]
    guard_output = run_cli(
        [*guard_common, "--runtime-profile", request.runtime_profile],
        legacy_args=[
            *guard_common,
            "--host-surface",
            request.legacy_host_surface,
            "--scheduler-owner",
            request.scheduler_owner,
            "--execution-mode",
            request.execution_mode,
        ],
    )
    guard = classify_host_guard_snapshot(guard_output)
    if guard.state is not HostGuardState.SELECTED:
        return _blocked_payload(
            request,
            stage="guard",
            reason=guard.reason or "quota guard has no selected Todo",
            guard_state=guard.state,
        )
    if guard.todo_id != request.todo_id:
        return _blocked_payload(
            request,
            stage="guard",
            reason=(
                "quota guard selected a different Todo: expected "
                f"{request.todo_id}, selected {guard.todo_id}"
            ),
            guard_state=guard.state,
        )
    if (
        guard.settlement_identity is not None
        and guard.settlement_identity != expected_identity
    ):
        return _blocked_payload(
            request,
            stage="guard",
            reason="quota guard settlement identity does not match the request",
            guard_state=HostGuardState.INVALID,
        )

    lifecycle_args = tuple(
        arg for arg in request.completion_args if arg != "--no-follow-up"
    )
    completion_output = run_cli(
        [*lifecycle_args, "--turn-instance-id", turn_instance_id]
    )
    completion = _json_object(completion_output)
    if completion is None:
        return _blocked_payload(
            request,
            stage="durable_writeback",
            reason="todo completion returned malformed JSON",
        )
    if not (
        completion.get("ok") is True
        and completion.get("completed") is True
        and completion.get("status") == "done"
    ):
        # Preserve the completion gate's typed failure payload unchanged.
        return completion_output

    settlement_result = completion.get("settlement_result")
    if (
        not _identity_matches(completion, expected_identity)
        or not isinstance(settlement_result, Mapping)
        or settlement_result.get("failure") is not None
    ):
        return _blocked_payload(
            request,
            stage="durable_writeback",
            reason="todo completion did not prove the expected settlement identity",
            completion=completion,
        )

    writeback_output = run_cli(
        [
            "refresh-state",
            "--goal-id",
            request.goal_id,
            "--agent-id",
            request.agent_id,
            "--classification",
            "mcp_completed_turn_writeback",
            "--delivery-batch-scale",
            "single_surface",
            "--delivery-outcome",
            "outcome_progress",
            "--todo-id",
            request.todo_id,
            "--turn-instance-id",
            turn_instance_id,
            "--completion-todo-id",
            request.todo_id,
            "--completion-turn-key",
            expected_identity.effect_id,
            "--no-global-sync",
            "--suppress-external-sinks",
        ]
    )
    writeback = _json_object(writeback_output)
    writeback_result = (
        writeback.get("settlement_result")
        if isinstance(writeback, Mapping)
        else None
    )
    if not (
        isinstance(writeback, Mapping)
        and writeback.get("ok") is True
        and _identity_matches(writeback, expected_identity)
        and isinstance(writeback_result, Mapping)
        and writeback_result.get("failure") is None
    ):
        reason = (
            str(writeback.get("error") or writeback.get("reason") or "")
            if isinstance(writeback, Mapping)
            else ""
        )
        return _blocked_payload(
            request,
            stage="durable_writeback",
            reason=(
                reason
                or "refresh-state did not prove the expected settlement identity"
            ),
            completion=completion,
        )

    spend_output = run_cli(
        [
            "quota",
            "spend-slot",
            "--goal-id",
            request.goal_id,
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
            "--agent-id",
            request.agent_id,
            "--todo-id",
            request.todo_id,
            "--turn-instance-id",
            turn_instance_id,
        ]
    )
    spend = _json_object(spend_output)
    spend_result = spend.get("settlement_result") if spend is not None else None
    spend_committed = bool(
        isinstance(spend, Mapping)
        and (
            spend.get("appended") is True
            or spend.get("idempotent_replay") is True
            or spend.get("receipt_repaired") is True
        )
    )
    if not (
        isinstance(spend, Mapping)
        and spend.get("ok") is True
        and spend_committed
        and _identity_matches(spend, expected_identity)
        and isinstance(spend_result, Mapping)
        and spend_result.get("failure") is None
    ):
        reason = (
            str(spend.get("error") or spend.get("reason") or "")
            if spend is not None
            else ""
        )
        return _blocked_payload(
            request,
            stage="quota_spend",
            reason=reason or "quota spend did not append a receipt",
            completion=completion,
        )

    terminal_closeout = None
    final_completion = completion
    if request.no_follow_up:
        terminal_output = run_cli(
            [*request.completion_args, "--turn-instance-id", turn_instance_id]
        )
        terminal_closeout = _json_object(terminal_output)
        terminal_result = (
            terminal_closeout.get("settlement_result")
            if terminal_closeout is not None
            else None
        )
        if not (
            isinstance(terminal_closeout, Mapping)
            and terminal_closeout.get("ok") is True
            and terminal_closeout.get("completed") is True
            and terminal_closeout.get("status") == "done"
            and terminal_closeout.get("completion_continuation") == "no_followup"
            and _identity_matches(terminal_closeout, expected_identity)
            and isinstance(terminal_result, Mapping)
            and terminal_result.get("failure") is None
        ):
            reason = (
                str(
                    terminal_closeout.get("error")
                    or terminal_closeout.get("reason")
                    or ""
                )
                if isinstance(terminal_closeout, Mapping)
                else ""
            )
            return _blocked_payload(
                request,
                stage="terminal_closeout",
                reason=(
                    reason
                    or "Todo terminal closeout did not prove the expected identity"
                ),
                completion=completion,
            )
        final_completion = terminal_closeout

    settlement_payload = {
        "ok": True,
        "guard_state": guard.state.value,
        "durable_writeback": writeback,
        "lifecycle_completion": completion,
        "quota_spend": spend,
    }
    if terminal_closeout is not None:
        settlement_payload["terminal_closeout"] = terminal_closeout

    return json.dumps(
        {
            "schema_version": HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION,
            "ok": True,
            "completed": True,
            "status": "done",
            "goal_id": request.goal_id,
            "todo_id": request.todo_id,
            "settlement_identity": expected_identity.as_dict(),
            "completion": final_completion,
            "settlement": settlement_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
