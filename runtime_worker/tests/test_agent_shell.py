import pytest

from runtime_worker.agent_shell import AgentShell, RunCommand


def test_command_rejects_bypass_permissions() -> None:
    with pytest.raises(ValueError, match="bypassPermissions"):
        RunCommand(prompt="x", model="claude", permission_mode="bypassPermissions")


def test_build_options_keeps_tool_boundaries() -> None:
    command = RunCommand(
        prompt="review",
        model="claude",
        tools=["Read"],
        allowed_tools=["Read"],
        disallowed_tools=["Bash"],
        cwd="/workspace",
    )
    options = AgentShell.build_options(command)
    assert options.tools == ["Read"]
    assert options.allowed_tools == ["Read"]
    assert options.disallowed_tools == ["Bash"]
    assert options.cwd == "/workspace"
