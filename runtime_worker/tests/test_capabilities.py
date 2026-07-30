from types import SimpleNamespace

import pytest

from runtime_worker import capabilities
from runtime_worker.main import _consume_release_digest


def test_service_mode_discovers_only_managed_agent_sdk(monkeypatch) -> None:
    versions = {
        "claude-agent-sdk": "0.2.110",
        "neomua-runtime-worker": "0.1.0",
    }
    monkeypatch.setattr(capabilities, "version", versions.__getitem__)
    monkeypatch.setattr(
        capabilities.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="2.1.191 (Claude Code)\n"),
    )
    assert capabilities.discover_runtime_capabilities(mode="service") == {
        "claude_agent_sdk": {
            "cli_version": "2.1.191",
            "sdk_version": "0.2.110",
            "harness_version": "0.1.0",
            "builtin_tools": ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
            "adapter_version": "0.1.0",
            "supports_tool_filters": True,
            "supports_per_tool_approval": True,
            "supports_mcp_injection": True,
            "permission_modes": ["default", "acceptEdits", "plan"],
        },
        "mcp_executables": [],
    }


def test_client_mode_never_uses_legacy_path_discovery() -> None:
    assert capabilities.discover_runtime_capabilities(mode="client") == {
        "mcp_executables": []
    }
    with pytest.raises(
        capabilities.CapabilityDetectionError,
        match="signed Adapter Registry",
    ):
        capabilities.discover_runtime_installations(mode="client")


def test_release_digest_is_consumed_before_agent_environment_is_used() -> None:
    source_environment = {
        "PATH": "/usr/bin",
        "RUNTIME_WORKER_RELEASE_DIGEST": "A" * 64,
    }

    assert _consume_release_digest(source_environment) == "a" * 64
    assert source_environment == {"PATH": "/usr/bin"}
