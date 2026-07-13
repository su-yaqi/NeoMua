import json
import stat
import zipfile

import pytest

from app.agent_management.capabilities import (
    canonical_digest,
    effective_tool_policy,
    scan_skill_archive,
    validate_mcp_config,
    validate_semver,
)
from app.agent_management.capability_models import McpTransport


def _skill_archive(path, *, extra: tuple[str, bytes, int] | None = None) -> None:
    manifest = """---
name: safe-skill
description: Safe declarative Skill
version: 1.2.3
platforms: [linux]
invocation_mode: discoverable
required_tools: [Read]
required_mcp_tools: []
config_schema: {}
content_types: [md, txt]
source: internal
---
# Safe Skill
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SKILL.md", manifest)
        archive.writestr("references/guide.txt", "guide")
        if extra:
            name, content, mode = extra
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, content)


def test_skill_scanner_accepts_declarative_archive(tmp_path) -> None:
    path = tmp_path / "skill.zip"
    _skill_archive(path)
    result = scan_skill_archive(path, slug="safe-skill", version="1.2.3")
    assert result.manifest["required_tools"] == ["Read"]
    assert [item["path"] for item in result.files] == [
        "SKILL.md",
        "references/guide.txt",
    ]


@pytest.mark.parametrize(
    ("name", "mode"),
    [("run.sh", stat.S_IFREG | 0o644), ("guide.txt", stat.S_IFREG | 0o755)],
)
def test_skill_scanner_rejects_script_or_executable(tmp_path, name, mode) -> None:
    path = tmp_path / "skill.zip"
    _skill_archive(path, extra=(name, b"echo unsafe", mode))
    with pytest.raises(ValueError):
        scan_skill_archive(path, slug="safe-skill", version="1.2.3")


def test_mcp_endpoint_policy_rejects_private_and_credentials() -> None:
    private = validate_mcp_config(
        McpTransport.STREAMABLE_HTTP,
        {"endpoint": "https://127.0.0.1/tools"},
    )
    credentials = validate_mcp_config(
        McpTransport.STREAMABLE_HTTP,
        {"endpoint": "https://user:pass@example.com/tools"},
    )
    assert any(item.code == "unsafe_mcp_endpoint" for item in private)
    assert any(item.code == "unsafe_mcp_endpoint" for item in credentials)


def test_stdio_mcp_never_accepts_shell_command() -> None:
    diagnostics = validate_mcp_config(
        McpTransport.STDIO,
        {"executable_key": "server", "args": ["--safe", "; rm -rf /"]},
    )
    assert any(item.code == "invalid_stdio_args" for item in diagnostics)


def test_tool_policy_merge_only_tightens() -> None:
    assert (
        effective_tool_policy("allow", "require_approval", "allow")
        == "require_approval"
    )
    assert effective_tool_policy("forbidden", "inherit", "allow") == "forbidden"
    assert effective_tool_policy("allow", "disabled", "inherit") == "disabled"


def test_canonical_digest_is_order_independent() -> None:
    left = {"b": [2, 1], "a": {"z": True}}
    right = json.loads('{"a":{"z":true},"b":[2,1]}')
    assert canonical_digest(left) == canonical_digest(right)
    assert validate_semver("1.2.3")
    assert not validate_semver("latest")
