import hashlib
import json
import stat
import uuid
import zipfile

import pytest

from app.agent_management import capabilities
from app.agent_management.capabilities import (
    ResolutionError,
    _load_skill_files,
    canonical_digest,
    effective_tool_policy,
    parse_skill_frontmatter,
    scan_skill_archive,
    validate_mcp_config,
    validate_semver,
)
from app.agent_management.capability_models import McpTransport, SkillVersion
from app.core.config import settings


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


def _custom_skill_archive(
    path,
    *,
    manifest_lines: list[str] | None = None,
    files: list[tuple[str, bytes, int | None]] | None = None,
) -> None:
    lines = manifest_lines or [
        "name: safe-skill",
        "description: Safe declarative Skill",
        "version: 1.2.3",
        "platforms: [linux]",
        "invocation_mode: discoverable",
        "required_tools: [Read]",
        "required_mcp_tools: []",
        "config_schema: {}",
        "content_types: [md, txt]",
        "source: internal",
    ]
    manifest = f"---\n{chr(10).join(lines)}\n---\n# Safe Skill\n".encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SKILL.md", manifest)
        for name, content, mode in files or []:
            info = zipfile.ZipInfo(name)
            if mode is not None:
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


def test_frontmatter_parser_supports_constrained_scalars_and_block_lists() -> None:
    parsed = parse_skill_frontmatter(
        """---
name: 'safe-skill'
enabled: true
empty:
platforms:
  - linux
  - "darwin"
config_schema: {'type': 'object'}
---
body
"""
    )
    assert parsed == {
        "name": "safe-skill",
        "enabled": True,
        "empty": [],
        "platforms": ["linux", "darwin"],
        "config_schema": {"type": "object"},
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("# no frontmatter", "must begin"),
        ("---\nname: safe", "not terminated"),
        ("---\n nested: value\n---\n", "unsupported nested"),
        ("---\nInvalid-Key: value\n---\n", "invalid key"),
        ("---\nitems: value\n  - nested\n---\n", "unsupported nested"),
    ],
)
def test_frontmatter_parser_rejects_unsupported_yaml(content, message) -> None:
    with pytest.raises(ValueError, match=message):
        parse_skill_frontmatter(content)


@pytest.mark.parametrize(
    ("version", "valid"),
    [
        ("0.0.0", True),
        ("1.2.3-alpha.1", True),
        ("1.2.3+build.5", True),
        ("1.2.3-01", False),
        ("01.2.3", False),
        ("1.2", False),
        ("1.2.3.4", False),
    ],
)
def test_semver_validation_requires_canonical_versions(version, valid) -> None:
    assert validate_semver(version) is valid


def test_skill_scanner_rejects_invalid_archive_and_missing_manifest(tmp_path) -> None:
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="valid ZIP"):
        scan_skill_archive(invalid, slug="safe-skill", version="1.2.3")

    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("README.md", "missing")
    with pytest.raises(ValueError, match="exactly one root SKILL.md"):
        scan_skill_archive(missing, slug="safe-skill", version="1.2.3")


@pytest.mark.parametrize(
    ("name", "content", "mode", "message"),
    [
        ("../escape.txt", b"escape", None, "unsafe archive path"),
        ("nested\\escape.txt", b"escape", None, "unsafe archive path"),
        ("link.txt", b"target", stat.S_IFLNK | 0o777, "symbolic links"),
        ("payload.py", b"print('x')", None, "unsupported Skill content type"),
        ("unknown.bin", b"binary", None, "unsupported Skill content type"),
        ("bad.txt", b"\xff", None, "not UTF-8"),
    ],
)
def test_skill_scanner_rejects_unsafe_entries(
    tmp_path, name, content, mode, message
) -> None:
    path = tmp_path / f"{uuid.uuid4().hex}.zip"
    _custom_skill_archive(path, files=[(name, content, mode)])
    with pytest.raises(ValueError, match=message):
        scan_skill_archive(path, slug="safe-skill", version="1.2.3")


def test_skill_scanner_enforces_file_and_expanded_size_limits(
    tmp_path, monkeypatch
) -> None:
    too_many = tmp_path / "too-many.zip"
    _custom_skill_archive(too_many, files=[("one.txt", b"1", None)])
    monkeypatch.setattr(capabilities, "MAX_SKILL_FILES", 1)
    with pytest.raises(ValueError, match="too many files"):
        scan_skill_archive(too_many, slug="safe-skill", version="1.2.3")

    monkeypatch.setattr(capabilities, "MAX_SKILL_FILES", 1000)
    monkeypatch.setattr(capabilities, "MAX_SKILL_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="file exceeds size"):
        scan_skill_archive(too_many, slug="safe-skill", version="1.2.3")

    monkeypatch.setattr(capabilities, "MAX_SKILL_FILE_BYTES", 1024)
    monkeypatch.setattr(capabilities, "MAX_SKILL_EXPANDED_BYTES", 10)
    with pytest.raises(ValueError, match="expanded size"):
        scan_skill_archive(too_many, slug="safe-skill", version="1.2.3")


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("name: another-skill", "name must match"),
        ("version: 1.2.4", "version must match"),
        ("invocation_mode: automatic", "invocation_mode is not supported"),
        ("platforms: linux", "platforms must be a string list"),
        ('required_tools: ["Read", 1]', "required_tools must be a string list"),
        ("scripts: [run]", "executable manifest field is forbidden"),
    ],
)
def test_skill_scanner_validates_manifest_contract(
    tmp_path, replacement, message
) -> None:
    baseline = [
        "name: safe-skill",
        "description: Safe declarative Skill",
        "version: 1.2.3",
        "platforms: [linux]",
        "invocation_mode: discoverable",
        "required_tools: [Read]",
        "required_mcp_tools: []",
        "config_schema: {}",
        "content_types: [md, txt]",
        "source: internal",
    ]
    key = replacement.split(":", 1)[0]
    lines = [line for line in baseline if not line.startswith(f"{key}:")]
    lines.append(replacement)
    path = tmp_path / f"{uuid.uuid4().hex}.zip"
    _custom_skill_archive(path, manifest_lines=lines)
    with pytest.raises(ValueError, match=message):
        scan_skill_archive(path, slug="safe-skill", version="1.2.3")


def test_skill_scanner_rejects_missing_fields_and_undeclared_types(tmp_path) -> None:
    missing = tmp_path / "missing-field.zip"
    lines = [
        "name: safe-skill",
        "version: 1.2.3",
        "platforms: [linux]",
        "invocation_mode: discoverable",
        "required_tools: []",
        "required_mcp_tools: []",
        "config_schema: {}",
        "content_types: [md]",
        "source: internal",
    ]
    _custom_skill_archive(missing, manifest_lines=lines)
    with pytest.raises(ValueError, match="frontmatter is missing: description"):
        scan_skill_archive(missing, slug="safe-skill", version="1.2.3")

    undeclared = tmp_path / "undeclared.zip"
    _custom_skill_archive(undeclared, files=[("image.png", b"image", None)])
    with pytest.raises(ValueError, match="not declared"):
        scan_skill_archive(undeclared, slug="safe-skill", version="1.2.3")


def test_mcp_validation_accepts_safe_routes_and_reports_all_config_errors() -> None:
    assert (
        validate_mcp_config(
            McpTransport.STREAMABLE_HTTP,
            {"endpoint": "https://mcp.example.test/tools"},
        )
        == []
    )
    assert (
        validate_mcp_config(
            McpTransport.STREAMABLE_HTTP,
            {"endpoint": "http://mcp.example.test/tools"},
            allow_http=True,
        )
        == []
    )
    diagnostics = validate_mcp_config(
        McpTransport.STDIO,
        {
            "executable_key": "bad key",
            "args": "not-a-list",
            "token": "secret",
            "max_concurrency": 0,
            "unknown": True,
        },
    )
    assert {item.code for item in diagnostics} == {
        "invalid_executable_key",
        "invalid_stdio_args",
        "mcp_limit_invalid",
        "secret_value_forbidden",
        "unknown_mcp_config_field",
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://public.example.test",
        "https://localhost/tools",
        "https://10.0.0.1/tools",
        "https://[::1]/tools",
        "https://user:pass@example.test/tools",
    ],
)
def test_mcp_validation_rejects_unsafe_endpoints(endpoint) -> None:
    diagnostics = validate_mcp_config(
        McpTransport.SSE,
        {"endpoint": endpoint},
    )
    assert [item.code for item in diagnostics] == ["unsafe_mcp_endpoint"]


def test_load_skill_files_verifies_archive_and_file_digests(
    tmp_path, monkeypatch
) -> None:
    archive_path = tmp_path / "skill.zip"
    _skill_archive(archive_path)
    archive_bytes = archive_path.read_bytes()
    with zipfile.ZipFile(archive_path) as archive:
        guide = archive.read("references/guide.txt")
    monkeypatch.setattr(settings, "ARTIFACT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "ARTIFACT_LOCAL_ROOT", str(tmp_path))
    version = SkillVersion(
        skill_id=uuid.uuid4(),
        version="1.2.3",
        content_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        storage_key="skill.zip",
        size=len(archive_bytes),
        manifest={
            "files": [
                {
                    "path": "references/guide.txt",
                    "sha256": hashlib.sha256(guide).hexdigest(),
                }
            ]
        },
        invocation_mode="discoverable",
    )
    assert _load_skill_files(version)[0]["content_base64"] == "Z3VpZGU="

    version.content_sha256 = "0" * 64
    with pytest.raises(ResolutionError) as mismatch:
        _load_skill_files(version)
    assert mismatch.value.diagnostics[0].code == "skill_content_digest_mismatch"

    version.content_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    version.manifest["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ResolutionError) as file_mismatch:
        _load_skill_files(version)
    assert file_mismatch.value.diagnostics[0].code == "skill_file_digest_mismatch"
