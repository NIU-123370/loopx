#!/usr/bin/env python3
"""Regression for the first modular CLI command seam.

This protects the initial extraction from ``loopx.cli`` into
``loopx.cli_commands``. The old public invocation should keep working
while command registration/handling moves behind a small module contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, env: dict[str, str] | None = None) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), payload
    return payload


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from loopx.cli_commands import (
        handle_demo_command,
        handle_check_command,
        handle_doctor_command,
        handle_ml_experiment_command,
        handle_new_project_prompt_command,
        handle_review_packet_command,
        handle_status_command,
        register_doctor_command,
        register_ml_experiment_commands,
        register_starter_commands,
        register_status_commands,
    )

    assert callable(handle_demo_command)
    assert callable(handle_check_command)
    assert callable(register_doctor_command)
    assert callable(register_ml_experiment_commands)
    assert callable(register_starter_commands)
    assert callable(register_status_commands)
    assert callable(handle_doctor_command)
    assert callable(handle_ml_experiment_command)
    assert callable(handle_new_project_prompt_command)
    assert callable(handle_review_packet_command)
    assert callable(handle_status_command)

    with tempfile.TemporaryDirectory(prefix="loopx-cli-command-module-") as raw_tmp:
        root = Path(raw_tmp)
        home = root / "home"
        home.mkdir()
        project = root / "project"
        registry = project / ".loopx" / "registry.json"
        runtime_root = root / "runtime"
        caller_registry = root / "caller-registry"
        caller_registry.mkdir()
        environment = os.environ.copy()
        # This contract exercises command wiring, not the caller's installed state.
        environment.update(
            HOME=str(home),
            LOOPX_REGISTRY=str(caller_registry),
            LOOPX_RUNTIME_ROOT=str(runtime_root),
            USERPROFILE=str(home),
        )

        def isolated_cli(*args: str) -> dict[str, object]:
            return run_cli(
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime_root),
                *args,
                env=environment,
            )

        doctor = isolated_cli("doctor")
        assert doctor.get("ok") is True, doctor
        assert "checks" in doctor, doctor

        prompt = isolated_cli(
            "new-project-prompt",
            "--project",
            str(root / "prompt-project"),
            "--goal-doc",
            "GOAL.md",
            "--goal-id",
            "command-module-fixture",
        )
        assert prompt.get("ok") is True, prompt
        assert prompt.get("goal_id") == "command-module-fixture", prompt
        assert "prompt" in prompt, prompt

        demo = isolated_cli(
            "demo",
            "--project",
            str(project),
            "--goal-id",
            "command-module-demo",
        )
        assert demo.get("ok") is True, demo
        assert demo.get("goal_id") == "command-module-demo", demo
        assert "quota" in demo, demo

        check = isolated_cli("check", "--scan-root", "README.md", "--limit", "1")
        assert check.get("ok") is True, check
        assert "summary" in check, check

        status = isolated_cli("status", "--limit", "1")
        assert status.get("ok") is True, status
        assert "attention_queue" in status, status

        review = isolated_cli(
            "review-packet",
            "--goal-id",
            "command-module-demo",
            "--limit",
            "1",
        )
        assert review.get("ok") is True, review
        assert review.get("goal_id") == "command-module-demo", review

        ml_preview = isolated_cli(
            "ml-experiment",
            "preview",
            "--experiment-id",
            "command-module-exp",
            "--primary-metric",
            "offline_auc",
            "--baseline-value",
            "0.42",
            "--candidate-value",
            "0.43",
            "--train-window",
            "train_2026w24",
            "--eval-window",
            "eval_2026w25",
            "--hypothesis-id",
            "h_command_module",
            "--mechanism-family",
            "routing",
            "--route",
            "route_a",
            "--positive-evidence",
            "compact_eval_delta",
            "--next-candidate",
            "holdout_eval",
        )
        assert ml_preview.get("ok") is True, ml_preview
        assert ml_preview.get("mode") == "default_off_advisory_preview", ml_preview
        assert (
            ml_preview.get("result", {}).get("experiment_id")
            == "command-module-exp"
        ), ml_preview
        assert ml_preview.get("pack", {}).get("pack") == "ml_experiment", ml_preview

    print("cli-command-module-contract-regression ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
