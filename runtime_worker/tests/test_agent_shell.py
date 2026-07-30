import pytest

from runtime_worker.agent_shell import (
    AgentShell,
    ClaudeCodeShell,
    CodexShell,
    RunCommand,
)


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


def test_codex_adapter_builds_explicit_engine_command() -> None:
    command = RunCommand(
        engine_type="codex",
        prompt="review",
        model="gpt-5.4",
        permission_mode="plan",
        cwd="/workspace",
        add_dirs=["/shared"],
    )
    assert CodexShell.build_argv(command, "/usr/local/bin/codex") == [
        "/usr/local/bin/codex",
        "exec",
        "--json",
        "--ephemeral",
        "--model",
        "gpt-5.4",
        "--sandbox",
        "read-only",
        "--cd",
        "/workspace",
        "--add-dir",
        "/shared",
        "review",
    ]


def test_client_claude_adapter_builds_direct_jsonl_command() -> None:
    command = RunCommand(
        engine_type="claude_code",
        prompt="review",
        model="sonnet",
        permission_mode="plan",
        tools=["Read", "Grep"],
        allowed_tools=["Read"],
        cwd="/workspace",
    )
    assert ClaudeCodeShell.build_argv(command, "/usr/local/bin/claude") == [
        "/usr/local/bin/claude",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "sonnet",
        "--permission-mode",
        "plan",
        "--no-session-persistence",
        "--tools",
        "Read,Grep",
        "--allowed-tools",
        "Read",
        "review",
    ]


def test_codex_adapter_rejects_unimplemented_tool_filter_contract() -> None:
    command = RunCommand(
        engine_type="codex",
        prompt="review",
        model="gpt-5.4",
        tools=["Read"],
    )
    with pytest.raises(ValueError, match="Codex tool filters"):
        CodexShell.build_argv(command, "codex")
