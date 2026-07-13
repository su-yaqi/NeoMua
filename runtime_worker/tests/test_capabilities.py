from types import SimpleNamespace

from runtime_worker import capabilities


def test_discover_harness_capabilities(monkeypatch) -> None:
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
    assert capabilities.discover_harness_capabilities() == {
        "claude_code": {
            "cli_version": "2.1.191",
            "sdk_version": "0.2.110",
            "harness_version": "0.1.0",
            "builtin_tools": ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
        },
        "mcp_executables": [],
    }
