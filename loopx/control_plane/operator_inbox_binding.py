from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

BINDING_SCHEMA_VERSION = "operator_inbox_binding_v0"


def local_private_config_digest(
    *, project: str | Path, config_path: str | Path
) -> str | None:
    """Hash one project-local private config without returning its contents."""

    root = Path(project).expanduser().resolve()
    relative = PurePosixPath(str(config_path or "").strip().replace("\\", "/"))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        return None
    path = (root / Path(*relative.parts)).resolve()
    try:
        path.relative_to(root)
        content = path.read_bytes()
    except (OSError, ValueError):
        return None
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def operator_inbox_binding(
    *,
    project: str | Path,
    config_path: str | Path,
    expected_digest: object = None,
) -> dict[str, Any]:
    """Return a public-safe config-binding status for one inbox owner lane."""

    expected = str(expected_digest or "").strip()
    actual = local_private_config_digest(project=project, config_path=config_path)
    if not expected:
        status = "unbound"
    elif actual is None:
        status = "unavailable"
    elif actual != expected:
        status = "drifted"
    else:
        status = "verified"
    return {
        "schema_version": BINDING_SCHEMA_VERSION,
        "status": status,
        "digest_recorded": bool(expected),
        "config_available": actual is not None,
        "binding_verified": status == "verified",
        "attention_required": status in {"drifted", "unavailable"},
        "private_config_returned": False,
        "config_digest_returned": False,
    }
