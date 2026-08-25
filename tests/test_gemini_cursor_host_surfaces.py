"""End-to-end host contract for the Gemini CLI and cursor-agent surfaces.

Installing discoverable files is not the same as being a usable LoopX host. The
generated `/loopx` facade tells the agent to run `start-goal ... --host-surface
<exact-current-host>`, so these tests execute that path for real: if either host
is missing from the CLI choices, the selection gate or the activation dispatch,
the facade dead-ends at argparse and the surface is decorative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from host_surface_cli_probes import (
    onboarding_setup_command_installs,
    selection_gate_offers_surface,
    start_goal_accepts_surface,
)

from loopx.agent_onboarding import _start_instruction, _surface_install_command
from loopx.host_loop_activation import (
    build_agent_type_catalog,
    build_host_loop_activation_packet,
    normalize_agent_type,
    scheduler_command_binding_for_agent_type,
)

NEW_HOSTS = ("gemini-cli", "cursor-agent")


@pytest.mark.parametrize("host_surface", NEW_HOSTS)
def test_start_goal_accepts_the_new_host_surface(tmp_path: Path, host_surface: str) -> None:
    payload = start_goal_accepts_surface(host_surface, tmp_path)
    activation = payload["command_pack"]["host_loop_activation"]
    assert activation["host_surface"] == {
        "gemini-cli": "gemini_cli_agent_loop",
        "cursor-agent": "cursor_agent_loop",
    }[host_surface]


@pytest.mark.parametrize("host_surface", NEW_HOSTS)
def test_host_selection_gate_offers_the_new_surfaces_and_its_rerun_command_works(
    tmp_path: Path, host_surface: str
) -> None:
    selection_gate_offers_surface(host_surface, tmp_path)


@pytest.mark.parametrize("agent_type", NEW_HOSTS)
def test_agent_onboarding_setup_command_installs_that_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_type: str,
) -> None:
    """agent-onboard hands back a setup command; executing it must provision the
    host it named, from any cwd."""
    monkeypatch.delenv("LOOPX_SKILLS_DIR", raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "GEMINI_HOME": str(tmp_path / "gemini"),
        "CURSOR_HOME": str(tmp_path / "cursor"),
    }

    home = tmp_path / ("gemini" if agent_type == "gemini-cli" else "cursor")
    onboarding_setup_command_installs(
        agent_type,
        outside,
        env,
        expected_skill=home / "skills" / "loopx" / "SKILL.md",
    )


@pytest.mark.parametrize("agent_type", NEW_HOSTS)
def test_agent_type_catalog_and_scheduler_binding(agent_type: str) -> None:
    """A host with no scheduler binding falls through to the generic default and
    the loop it actually runs stops being visible to the control plane."""
    catalog = build_agent_type_catalog()
    entry = next(
        item
        for item in catalog["canonical_agent_types"]
        if item["agent_type"] == agent_type
    )
    assert entry["display_name"]
    assert entry["host_loop"]
    # The bare product name is what a user types.
    assert agent_type.split("-")[0] in entry["accepted_inputs"]
    assert normalize_agent_type(agent_type.split("-")[0]) == agent_type
    assert scheduler_command_binding_for_agent_type(agent_type) == {
        "runtime_profile": "generic_cli"
    }


@pytest.mark.parametrize("agent_type", NEW_HOSTS)
def test_activation_claims_no_host_loop_these_clis_do_not_have(agent_type: str) -> None:
    """Neither CLI owns a goal primitive or an automation scheduler. The packet
    has to say so: an overstated capability here is what makes an agent claim
    autonomous setup it cannot deliver."""
    packet = build_host_loop_activation_packet(
        agent_type=agent_type,
        goal_id="surface-goal",
        agent_id="probe-agent",
        registered_agents=["probe-agent"],
    )
    assert packet["activation_method"] == "run_agent_cli_loop_gated_by_quota"
    assert packet["host_mutation"]["cli_can_mutate_directly"] is False
    assert packet["host_mutation"]["host_loop_primitive"] is None
    assert packet["host_mutation"]["loop_driver"] == "agent_cli_turn_loop"
    assert packet["setup_command"] == _surface_install_command(agent_type, "loopx", ".")
    assert "quota should-run" in " ".join(packet["activation_steps"])
    assert "quota should-run" in _start_instruction(agent_type)


def test_cursor_activation_names_the_registered_mcp_server() -> None:
    """cursor-agent is the one new host that also gets the LoopX MCP server, so
    its activation must point at it rather than leave the agent shelling out."""
    packet = build_host_loop_activation_packet(
        agent_type="cursor-agent",
        goal_id="surface-goal",
        agent_id="probe-agent",
        registered_agents=["probe-agent"],
    )
    mutation = packet["host_mutation"]
    assert mutation["host_mcp_server"] == "loopx"
    assert mutation["host_mcp_config"] == "CURSOR_HOME/mcp.json"
