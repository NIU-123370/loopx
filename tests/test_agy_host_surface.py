"""End-to-end host contract for the Antigravity CLI (agy) surface.

Installing discoverable files is not the same as being a usable LoopX host. The
generated `/loopx` facade tells the agent to run `start-goal ... --host-surface
<exact-current-host>`, so these tests execute that path for real: if agy is
missing from the CLI choices, the selection gate or the activation dispatch,
the facade dead-ends at argparse and the surface is decorative.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from host_surface_cli_probes import (
    onboarding_setup_command_installs,
    selection_gate_offers_surface,
    start_goal_accepts_surface,
)

from loopx.agent_onboarding import _start_instruction, _surface_install_command
from loopx.host_loop_activation import (
    _heartbeat_commands,
    build_agent_type_catalog,
    build_host_loop_activation_packet,
    normalize_agent_type,
    scheduler_command_binding_for_agent_type,
)
from loopx.slash_command_install import install_slash_commands
from loopx.agy_goal_mode import (
    AGY_GOAL_CANCELLED_TOKEN,
    AGY_GOAL_COMMAND,
    AGY_GOAL_COMMAND_DESCRIPTION,
    AGY_GOAL_COMPLETE_TOKEN,
    AGY_NATIVE_WAKE_FACTS,
    AGY_NATIVE_WAKE_TOOLS,
    agy_home,
)

HOST_SURFACE = "agy"


def test_start_goal_accepts_the_agy_host_surface(tmp_path: Path) -> None:
    payload = start_goal_accepts_surface(HOST_SURFACE, tmp_path)
    activation = payload["command_pack"]["host_loop_activation"]
    assert activation["host_surface"] == "agy_agent_loop"


def test_host_selection_gate_offers_agy_and_its_rerun_command_works(
    tmp_path: Path,
) -> None:
    selection_gate_offers_surface(HOST_SURFACE, tmp_path)


def test_agent_onboarding_setup_command_installs_the_agy_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOOPX_SKILLS_DIR", raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
    }
    if "PYTHONPATH" in os.environ:  # keep hermetic when run from a worktree
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]

    # agy reads skills from its fixed ~/.gemini/antigravity-cli/skills root,
    # flat layout (loopx.md), not a per-skill directory.
    onboarding_setup_command_installs(
        HOST_SURFACE,
        outside,
        env,
        expected_skill=(
            tmp_path / "home" / ".gemini" / "antigravity-cli" / "skills" / "loopx.md"
        ),
    )


def test_agy_home_is_the_documented_fixed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The official CLI documents no home override, so LoopX exposes none:
    # only HOME itself (tests stay hermetic) and the internal injection
    # parameter move the root.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert agy_home() == tmp_path / "home" / ".gemini" / "antigravity-cli"
    assert agy_home(str(tmp_path / "explicit")) == tmp_path / "explicit"


def test_installer_preserves_user_owned_agy_skill(tmp_path: Path) -> None:
    """The skills root is shared with the user's own skills; an unmarked file
    must never be overwritten, and a rerun over a managed file is a no-op."""
    skills_dir = tmp_path / "agy-home" / "skills"
    skill_path = skills_dir / "loopx.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("user-owned skill body\n", encoding="utf-8")

    payload = install_slash_commands(
        execute=True,
        surfaces=["agy"],
        agy_home=str(tmp_path / "agy-home"),
    )
    statuses = {
        (item["surface"], item["command"]): item["status"] for item in payload["installed"]
    }
    assert statuses[("agy", "/loopx")] == "skipped_user_file"
    assert skill_path.read_text(encoding="utf-8") == "user-owned skill body\n"

    skill_path.write_text(
        "<!-- loopx-managed-slash-command:v1 command=/loopx surface=claude-skills -->\nold\n",
        encoding="utf-8",
    )
    payload = install_slash_commands(
        execute=True,
        surfaces=["agy"],
        agy_home=str(tmp_path / "agy-home"),
    )
    statuses = {
        (item["surface"], item["command"]): item["status"] for item in payload["installed"]
    }
    assert statuses[("agy", "/loopx")] == "updated"
    assert "user-owned skill body" not in skill_path.read_text(encoding="utf-8")

    retire = install_slash_commands(
        execute=True,
        uninstall=True,
        surfaces=["agy"],
        agy_home=str(tmp_path / "agy-home"),
    )
    assert not skill_path.exists()
    assert retire["ok"] is True


def test_agent_type_catalog_and_scheduler_binding() -> None:
    """A host with no scheduler binding falls through to the generic default and
    the loop it actually runs stops being visible to the control plane."""
    catalog = build_agent_type_catalog()
    entry = next(
        item
        for item in catalog["canonical_agent_types"]
        if item["agent_type"] == HOST_SURFACE
    )
    assert entry["display_name"] == "Antigravity CLI"
    assert entry["host_loop"]
    assert "gated" not in entry["host_loop"].lower()
    assert "advisory" in entry["host_loop"].lower()
    # The bare product name is what a user types.
    assert HOST_SURFACE in entry["accepted_inputs"]
    assert normalize_agent_type("agy") == HOST_SURFACE
    assert normalize_agent_type("antigravity") == HOST_SURFACE
    assert normalize_agent_type("antigravity-cli") == HOST_SURFACE
    assert scheduler_command_binding_for_agent_type(HOST_SURFACE) == {
        "runtime_profile": "generic_cli"
    }


def test_activation_binds_native_goal_and_wake_with_advisory_quota_entry() -> None:
    """Antigravity CLI owns both a native goal primitive (the `/goal` command
    with host-side forced continuation until `<!-- GOAL_COMPLETE -->`,
    live-verified in the TUI and headless `-p`) and a native in-session
    scheduler (the `schedule` tool plus background-task/subagent wakes).
    The packet has to state exactly that capability envelope: bind the
    objective via `/goal`, arm self-wakes with `schedule`, state advisory
    quota pacing without claiming host continuation interception, and admit
    the loop dies with the session — an overstated capability here is what
    makes an agent claim autonomous setup it cannot deliver."""
    packet = build_host_loop_activation_packet(
        agent_type=HOST_SURFACE,
        goal_id="surface-goal",
        agent_id="probe-agent",
        registered_agents=["probe-agent"],
    )
    assert (
        packet["activation_method"] == "bind_native_goal_with_advisory_quota_entry"
    )
    assert packet["host_mutation"]["cli_can_mutate_directly"] is False
    # Both native primitives are real and must be named, not denied.
    assert (
        packet["host_mutation"]["host_loop_primitive"] == "agy-/goal-and-schedule-tool"
    )
    assert (
        packet["host_mutation"]["loop_driver"]
        == "agy_native_goal_loop_with_schedule_wakes"
    )
    # The quota claim must be honest: advisory guidance, no host hook.
    assert (
        packet["host_mutation"]["quota_gate_enforcement"] == "advisory_only"
    )
    assert packet["host_mutation"]["native_goal_command"] == "/goal"
    assert packet["host_mutation"]["goal_complete_token"] == AGY_GOAL_COMPLETE_TOKEN
    assert packet["host_mutation"]["goal_cancelled_token"] == AGY_GOAL_CANCELLED_TOKEN
    assert "schedule" in packet["host_mutation"]["native_wake_tools"]
    assert "manage_task" in packet["host_mutation"]["native_wake_tools"]
    # The loop dies with the CLI session; the gate text must say so instead of
    # promising unattended heartbeat support.
    gate = packet["host_mutation"]["missing_host_tool_gate"]
    assert "only" in gate
    assert "alive" in gate
    assert "daemon" in gate
    assert "advisory" in gate
    steps = " ".join(packet["activation_steps"])
    assert "`/goal <task_body>`" in steps
    assert AGY_GOAL_COMPLETE_TOKEN in steps
    assert "`schedule` tool" in steps
    assert "MaxIterations" in steps
    assert "quota should-run" in steps
    assert "no host scheduler to fall back on" not in steps
    assert packet["setup_command"] == _surface_install_command(HOST_SURFACE, "loopx", ".")
    assert "quota should-run" in _start_instruction(HOST_SURFACE)
    assert "/goal" in _start_instruction(HOST_SURFACE)
    assert (
        packet["entry_command_hint"]
        == "the LoopX skill installed in ~/.gemini/antigravity-cli/skills"
    )
    # Ensure rendered heartbeat commands and scope contain no machine-gate semantics
    assert "gated" not in packet["activation_input_command"].lower()
    assert "advisory" in packet["activation_input_command"].lower()
    hb = _heartbeat_commands(
        goal_id="surface-goal",
        agent_type=HOST_SURFACE,
        cli_bin="loopx",
        agent_id="probe-agent",
    )
    assert "gated" not in hb["heartbeat_prompt"].lower()
    assert "gated" not in hb["heartbeat_prompt_json"].lower()
    assert "advisory" in hb["heartbeat_prompt"].lower()
    assert "advisory" in hb["heartbeat_prompt_json"].lower()


def test_native_goal_and_wake_facts_match_the_live_probes() -> None:
    """The host-facts constants are the single source the activation packet,
    README and PR narrative cite; they must stay pinned to what the live
    agy 1.1.18 probes actually demonstrated."""
    assert AGY_GOAL_COMMAND == "/goal"
    assert "completely finished" in AGY_GOAL_COMMAND_DESCRIPTION
    assert AGY_GOAL_COMPLETE_TOKEN == "<!-- GOAL_COMPLETE -->"
    assert AGY_GOAL_CANCELLED_TOKEN == "<!-- GOAL_CANCELLED -->"
    facts = " ".join(AGY_NATIVE_WAKE_FACTS)
    assert "DurationSeconds" in facts
    assert "Prompt" in facts
    assert "MaxIterations" in facts
    assert "hooks.json" in facts
    assert set(AGY_NATIVE_WAKE_TOOLS) >= {
        "schedule",
        "manage_task",
        "invoke_subagent",
        "send_message",
        "manage_inbox",
    }
