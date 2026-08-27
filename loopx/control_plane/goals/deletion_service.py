"""Canonical owner-confirmed deletion for stopped Goals."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

from ...file_lock import exclusive_file_lock
from ...history import load_registry
from ...registry import atomic_write_json
from ...registry_writability import probe_registry_write_path
from ..runtime.time import now_local_iso
from .activation import GoalActivationState, goal_activation_state
from .activation_service import (
    GoalActivationAuthorityRouteMode,
    _goal_or_none,
    _same_path,
    _source_and_target,
)


GOAL_DELETION_SCHEMA_VERSION = "loopx_goal_deletion_v1"
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def _require_opaque_id(value: str, *, field: str) -> str:
    """Reject any value that is not a compact opaque id; safe for path segments."""

    token = str(value or "").strip()
    if not _OPAQUE_ID.fullmatch(token):
        raise ValueError(f"{field} must be a compact opaque id")
    return token


def _registry_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup_path(path: Path, timestamp: str, nonce: str) -> Path:
    compact_timestamp = timestamp.replace(":", "").replace("-", "")
    return path.with_name(
        f"{path.name}.goal-delete-{compact_timestamp}-{nonce}.bak"
    )


def _create_backup(path: Path, *, timestamp: str) -> Path:
    """Create an independent snapshot without ever overwriting an old backup."""

    source_path = path.expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError(f"registry path is not a regular file: {source_path}")
    for _ in range(8):
        backup = _backup_path(source_path, timestamp, uuid.uuid4().hex)
        try:
            descriptor = os.open(
                backup,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            continue
        try:
            with source_path.open("rb") as source, os.fdopen(descriptor, "wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            shutil.copystat(source_path, backup)
        except Exception:
            backup.unlink(missing_ok=True)
            raise
        return backup
    raise FileExistsError(f"could not allocate a unique Goal deletion backup for {source_path}")


def _remove_goal(payload: dict[str, Any], goal_id: str) -> tuple[dict[str, Any], bool]:
    goals = payload.get("goals")
    if not isinstance(goals, list):
        raise ValueError("registry goals must be a list")
    retained = [
        goal
        for goal in goals
        if not (isinstance(goal, dict) and str(goal.get("id") or "") == goal_id)
    ]
    changed = len(retained) != len(goals)
    updated = dict(payload)
    updated["goals"] = retained
    if changed:
        updated["updated_at"] = now_local_iso()
    return updated, changed


def _resolve_route(goal_id: str, registry_path: Path) -> dict[str, Any]:
    route = _source_and_target(
        registry_path=registry_path,
        goal_id=goal_id,
        target_state=GoalActivationState.STOPPED,
        runtime_root_override=None,
    )
    source_available = route.mode is not GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK
    source_payload = load_registry(route.source_registry) if source_available else None
    target_payload = load_registry(route.target_registry)
    source_goal = _goal_or_none(source_payload, goal_id) if source_payload else None
    target_goal = _goal_or_none(target_payload, goal_id)
    goal = source_goal or target_goal
    if goal is None:
        raise ValueError(f"goal id not found in registry: {goal_id}")
    if goal_activation_state(goal) is not GoalActivationState.STOPPED:
        raise ValueError("stop the Goal before deleting it")
    if target_goal is None:
        raise ValueError("global registry does not contain the Goal projection")
    return {
        "source_registry": route.source_registry,
        "target_registry": route.target_registry,
        "source_available": source_available,
        "source_goal": source_goal,
        "target_goal": target_goal,
        "same_registry": _same_path(route.source_registry, route.target_registry),
    }


def _check_writability(paths: list[Path]) -> dict[str, Any] | None:
    writability = [
        probe_registry_write_path(path, create_parent=False) for path in paths
    ]
    return next((item for item in writability if not item.get("ok")), None)


def _registry_paths(source_registry: Path, target_registry: Path, same_registry: bool) -> list[Path]:
    paths = [target_registry]
    if not same_registry:
        paths.append(source_registry)
    return sorted(paths, key=lambda item: str(item))


def _load_locked_payloads(
    *,
    source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
    goal_id: str,
    expected_state_fingerprint: str | None,
    payload: dict[str, Any],
) -> dict[Path, dict[str, Any]] | None:
    current_source = load_registry(source_registry) if source_available else None
    current_target = load_registry(target_registry)
    if expected_state_fingerprint is not None:
        current_fingerprint = _registry_fingerprint(target_registry)
        if current_fingerprint != expected_state_fingerprint:
            payload.update({
                "ok": False,
                "stale": True,
                "error_kind": "goal_registry_changed",
                "error": "Goal registry changed after preview; regenerate the deletion preview",
                "current_state_fingerprint": current_fingerprint,
            })
            return None

    source_goal = _goal_or_none(current_source, goal_id) if current_source else None
    target_goal = _goal_or_none(current_target, goal_id)
    locked_goal = source_goal or target_goal
    if locked_goal is None or target_goal is None:
        raise ValueError("Goal disappeared before deletion; refresh and retry")
    if goal_activation_state(locked_goal) is not GoalActivationState.STOPPED:
        raise ValueError("Goal activation changed; stop the Goal before deleting it")
    current_payloads = {target_registry: current_target}
    if source_available and not same_registry:
        if current_source is None or source_goal is None:
            raise ValueError("Goal source registry changed; refresh and retry")
        current_payloads[source_registry] = current_source
    return current_payloads


def _updated_payloads(
    current_payloads: dict[Path, dict[str, Any]], goal_id: str
) -> dict[Path, dict[str, Any]]:
    updated_payloads: dict[Path, dict[str, Any]] = {}
    for path, current in current_payloads.items():
        updated, changed = _remove_goal(current, goal_id)
        if not changed:
            raise ValueError("Goal disappeared before deletion; refresh and retry")
        updated_payloads[path] = updated
    return updated_payloads


def _write_deletion(
    *,
    current_payloads: dict[Path, dict[str, Any]],
    updated_payloads: dict[Path, dict[str, Any]],
    source_registry: Path,
    target_registry: Path,
    source_available: bool,
    goal_id: str,
    payload: dict[str, Any],
) -> None:
    written_paths: list[Path] = []
    try:
        timestamp = now_local_iso()
        for path in current_payloads:
            backup = _create_backup(path, timestamp=timestamp)
            payload["backup_paths"].append(str(backup))
        for path in sorted(updated_payloads, key=lambda item: str(item)):
            atomic_write_json(path, updated_payloads[path], preserve_mode=True)
            written_paths.append(path)
        source_after = load_registry(source_registry) if source_available else None
        target_after = load_registry(target_registry)
        source_missing = not source_after or _goal_or_none(source_after, goal_id) is None
        global_missing = _goal_or_none(target_after, goal_id) is None
        payload["readback"] = {
            "source_missing": source_missing,
            "global_missing": global_missing,
            "verified": source_missing and global_missing,
        }
        payload["written"] = bool(written_paths)
        payload["ok"] = bool(payload["readback"]["verified"])
        payload["partial_write"] = bool(payload["written"] and not payload["ok"])
        if not payload["ok"]:
            payload["error"] = "Goal deletion readback did not verify"
    except Exception:
        for path in written_paths:
            atomic_write_json(path, current_payloads[path], preserve_mode=True)
        raise


def _execute_deletion(
    *,
    source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
    goal_id: str,
    expected_state_fingerprint: str | None,
    payload: dict[str, Any],
) -> None:
    """Apply deletion and read it back while registry locks are held."""

    paths = _registry_paths(source_registry, target_registry, same_registry)
    with ExitStack() as stack:
        for path in paths:
            stack.enter_context(
                exclusive_file_lock(path, operation="delete_stopped_goal")
            )
        current_payloads = _load_locked_payloads(
            source_registry=source_registry,
            target_registry=target_registry,
            source_available=source_available,
            same_registry=same_registry,
            goal_id=goal_id,
            expected_state_fingerprint=expected_state_fingerprint,
            payload=payload,
        )
        if current_payloads is None:
            return
        _write_deletion(
            current_payloads=current_payloads,
            updated_payloads=_updated_payloads(current_payloads, goal_id),
            source_registry=source_registry,
            target_registry=target_registry,
            source_available=source_available,
            goal_id=goal_id,
            payload=payload,
        )


def delete_stopped_goal(
    *,
    registry_path: Path,
    goal_id: str,
    execute: bool = False,
    expected_state_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Preview or permanently remove one stopped Goal from its registries.

    Goal data such as state files and project files is intentionally retained;
    deletion removes only the registry entries that make the Goal visible to
    the LoopX control plane. When an expected fingerprint is supplied, the
    target registry is checked again while both registry locks are held.
    """

    normalized_goal_id = _require_opaque_id(goal_id, field="goal_id")
    route = _resolve_route(normalized_goal_id, Path(registry_path))

    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": GOAL_DELETION_SCHEMA_VERSION,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": normalized_goal_id,
        "source_registry": str(route["source_registry"]),
        "target_global_registry": str(route["target_registry"]),
        "source_registry_present": route["source_goal"] is not None,
        "global_registry_present": route["target_goal"] is not None,
        "written": False,
        "partial_write": False,
        "backup_paths": [],
        "readback": {
            "source_missing": route["source_goal"] is None,
            "global_missing": False,
            "verified": False,
        },
    }
    if not execute:
        return payload

    paths = [route["target_registry"]]
    if not route["same_registry"]:
        paths.append(route["source_registry"])
    writability = _check_writability(paths)
    if writability is not None:
        payload.update(
            {
                "ok": False,
                "error_kind": "goal_registry_write_denied",
                "error": str(writability.get("error") or "Goal registry is not writable"),
                "recommended_action": writability.get("recommended_action"),
            }
        )
        return payload

    _execute_deletion(
        source_registry=route["source_registry"],
        target_registry=route["target_registry"],
        source_available=route["source_available"],
        same_registry=route["same_registry"],
        goal_id=normalized_goal_id,
        expected_state_fingerprint=expected_state_fingerprint,
        payload=payload,
    )
    return payload