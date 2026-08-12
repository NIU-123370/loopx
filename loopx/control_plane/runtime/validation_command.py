from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Frozen wire id shared with capabilities/issue_fix. Kept verbatim for wire
# compatibility with existing caller-repo-branch receipts.
CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION = "issue_fix_validation_command_v0"


def run_caller_validation(
    workspace: Path,
    *,
    validation_command: str,
    validation_label: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run a caller-approved validation command and return a privacy-safe receipt.

    The command is ``shlex.split`` (no shell) and run with ``cwd=workspace``.
    Only ``exit_code`` and the boolean ``passed`` are recorded; command stdout,
    stderr, and local paths are deliberately not captured. A timeout raises
    ``subprocess.TimeoutExpired``; callers decide whether to convert that into
    a failure receipt.
    """
    argv = shlex.split(validation_command)
    if not argv:
        raise ValueError("validation_command must not be empty")
    result = subprocess.run(
        argv,
        cwd=workspace,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    return {
        "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
        "command_label": validation_label or "caller-declared validation",
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }


def require_validation_passed(step: Mapping[str, Any]) -> None:
    """Raise ``RuntimeError`` unless the validation step ``passed``."""
    if step.get("passed") is not True:
        raise RuntimeError(
            f"{step.get('command_label')} failed with exit code {step.get('exit_code')}"
        )
