from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.quota.scheduler_ack import _commit_scheduler_state
from loopx.control_plane.scheduler.state import scheduler_state_path


GOAL_ID = "scheduler-heartbeat-commit-runtime"
AGENT_ID = "codex-main-control"
SURFACE = "codex_app"
STATE_KEY = "scheduler_hint.codex_app.stateful_backoff"
RRULE_15 = "FREQ=MINUTELY;INTERVAL=15"
RRULE_30 = "FREQ=MINUTELY;INTERVAL=30"


def _facts(
    *,
    progression_index: int,
    generated_at: str,
    expected_rrule: str,
    applied_rrule: str,
    operation: str = "ack",
) -> dict[str, object]:
    return {
        "reset_token": "reset-runtime",
        "identity_signature": "identity-runtime",
        "progression_index": progression_index,
        "progression_minutes": [15, 30],
        "expected_rrule": expected_rrule,
        "applied_rrule": applied_rrule,
        "observed_host_rrule": applied_rrule,
        "cadence_class": "active_work",
        "stale_tolerance_minutes": 2,
        "generated_at": generated_at,
        "source": "quota_scheduler_ack",
        "ack_needed": True,
        "apply_needed": operation != "ack",
        "host_match_observed": False,
        "prior_host_update_failures": [],
    }


def _commit(
    runtime_root: Path,
    *,
    outcome: str,
    facts: dict[str, object],
    failure: dict[str, object] | None = None,
) -> dict[str, object]:
    return _commit_scheduler_state(
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        surface=SURFACE,
        state_key=STATE_KEY,
        outcome=outcome,
        facts=facts,
        failure=failure,
        execute=True,
    )


def test_python_adapter_uses_native_command_for_write_and_replay(tmp_path: Path) -> None:
    facts = _facts(
        progression_index=0,
        generated_at="2026-08-24T08:00:00Z",
        expected_rrule=RRULE_15,
        applied_rrule=RRULE_15,
    )
    written = _commit(tmp_path, outcome="ack", facts=facts)
    assert written["status"] == "written"
    assert written["written"] is True

    replayed = _commit(
        tmp_path,
        outcome="ack",
        facts={**facts, "generated_at": "2026-08-24T08:01:00Z"},
    )
    assert replayed["status"] == "replayed"
    assert replayed["replayed"] is True
    assert replayed["state_digest"] == written["state_digest"]


def test_native_transport_preserves_scheduler_failure_cache(tmp_path: Path) -> None:
    first = _commit(
        tmp_path,
        outcome="ack",
        facts=_facts(
            progression_index=0,
            generated_at="2026-08-24T08:00:00Z",
            expected_rrule=RRULE_15,
            applied_rrule=RRULE_15,
        ),
    )
    failure_facts = _facts(
        progression_index=1,
        generated_at="2026-08-24T08:01:00Z",
        expected_rrule=RRULE_30,
        applied_rrule=RRULE_15,
        operation="failure",
    )
    failure = _commit(
        tmp_path,
        outcome="failure",
        facts=failure_facts,
        failure={
            "target_rrule": RRULE_30,
            "observed_host_rrule": RRULE_15,
            "failure_kind": "timeout",
            "apply_needed": True,
        },
    )
    assert first["status"] == "written"
    assert failure["status"] == "written"
    assert failure["failure_count"] == 1


def test_native_transport_distinguishes_next_failure_after_cache_refresh(
    tmp_path: Path,
) -> None:
    _commit(
        tmp_path,
        outcome="ack",
        facts=_facts(
            progression_index=0,
            generated_at="2026-08-24T08:00:00Z",
            expected_rrule=RRULE_15,
            applied_rrule=RRULE_15,
        ),
    )
    failure_facts = _facts(
        progression_index=1,
        generated_at="2026-08-24T08:01:00Z",
        expected_rrule=RRULE_30,
        applied_rrule=RRULE_15,
        operation="failure",
    )
    first = _commit(
        tmp_path,
        outcome="failure",
        facts=failure_facts,
        failure={
            "target_rrule": RRULE_30,
            "observed_host_rrule": RRULE_15,
            "failure_kind": "timeout",
            "apply_needed": True,
        },
    )
    retry = _commit(
        tmp_path,
        outcome="failure",
        facts={**failure_facts, "generated_at": "2026-08-24T08:01:30Z"},
        failure={
            "target_rrule": RRULE_30,
            "observed_host_rrule": RRULE_15,
            "failure_kind": "timeout",
            "apply_needed": True,
        },
    )
    assert first["failure_count"] == 1
    assert retry["status"] == "replayed"

    second = _commit(
        tmp_path,
        outcome="failure",
        facts={
            **failure_facts,
            "generated_at": "2026-08-24T08:02:00Z",
            "prior_host_update_failures": first["state"]["host_update_failures"],
        },
        failure={
            "target_rrule": RRULE_30,
            "observed_host_rrule": RRULE_15,
            "failure_kind": "timeout",
            "apply_needed": True,
        },
    )
    assert second["status"] == "written"
    assert second["failure_count"] == 2


def test_python_adapter_does_not_use_managed_scheduler_handler() -> None:
    source = Path("loopx/control_plane/quota/scheduler_ack.py").read_text(
        encoding="utf-8"
    )
    handlers = Path("loopx/control_plane/effect_runtime_handlers.ts").read_text(
        encoding="utf-8"
    )
    assert "effect_runtime_result(\"scheduler.heartbeat.commit\"" not in source
    assert '"scheduler.heartbeat.commit"' not in handlers
    assert Path(
        "loopx/control_plane/scheduler/heartbeat_commit_cli.ts"
    ).exists()


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        ({"execute": "false"}, "execute must be a boolean"),
        ({"prior_host_update_failures": "not-a-list"}, "prior_host_update_failures must be an array"),
    ],
)
def test_native_transport_returns_typed_validation_errors(
    tmp_path: Path, facts: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _commit(
            tmp_path,
            outcome="ack",
            facts={
                **_facts(
                    progression_index=0,
                    generated_at="2026-08-24T08:00:00Z",
                    expected_rrule=RRULE_15,
                    applied_rrule=RRULE_15,
                ),
                **facts,
            },
        )


def test_native_result_persists_canonical_state_without_python_digest(tmp_path: Path) -> None:
    result = _commit(
        tmp_path,
        outcome="ack",
        facts=_facts(
            progression_index=0,
            generated_at="2026-08-24T08:00:00Z",
            expected_rrule=RRULE_15,
            applied_rrule=RRULE_15,
        ),
    )
    path = scheduler_state_path(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        surface=SURFACE,
        state_key=STATE_KEY,
    )
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["last_applied_rrule"] == RRULE_15
    assert result["state_digest"].startswith("sha256:")
