"""Validation and deterministic assembly for v0.5 managed capabilities."""

import base64
import hashlib
import io
import ipaddress
import json
import re
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.agent_management.capability_models import (
    AgentDraftMcp,
    AgentDraftPlugin,
    AgentDraftSkill,
    AgentDraftToolPolicy,
    McpServer,
    McpServerRevision,
    McpTargetBinding,
    McpTargetStatus,
    McpToolSnapshot,
    McpTransport,
    McpValidationAttempt,
    NamespaceToolPolicy,
    Plugin,
    PluginVersion,
    SkillDefinition,
    SkillVersion,
    ToolDefinition,
    ToolSource,
)
from app.agent_management.catalog import (
    MAX_TIMEOUT_SECONDS,
    Diagnostic,
    validate_config,
)
from app.agent_management.models import AgentDefinition, AgentDraft, HarnessProfile
from app.core.config import settings
from app.models import LlmModelDefinition, LlmProviderConfig, LlmProviderModel
from app.runtime.artifacts.local_storage import LocalArtifactStorage
from app.runtime.artifacts.storage import ArtifactStorage
from app.runtime.models import RuntimeProfile

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
ALLOWED_SKILL_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
}
FORBIDDEN_SKILL_SUFFIXES = {
    ".sh",
    ".py",
    ".js",
    ".ts",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bat",
    ".cmd",
    ".ps1",
    ".jar",
    ".docm",
    ".xlsm",
    ".pptm",
}
MAX_SKILL_FILES = 1000
MAX_SKILL_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_SKILL_FILE_BYTES = 10 * 1024 * 1024
SKILL_REQUIRED_FIELDS = {
    "name",
    "description",
    "version",
    "platforms",
    "invocation_mode",
    "required_tools",
    "required_mcp_tools",
    "config_schema",
    "content_types",
    "source",
}
BUILTIN_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "Read",
        "display_name": "Read",
        "risk_level": "low",
        "baseline_policy": "allow",
        "supports_approval": True,
    },
    {
        "tool_key": "Glob",
        "display_name": "Glob",
        "risk_level": "low",
        "baseline_policy": "allow",
        "supports_approval": True,
    },
    {
        "tool_key": "Grep",
        "display_name": "Grep",
        "risk_level": "low",
        "baseline_policy": "allow",
        "supports_approval": True,
    },
    {
        "tool_key": "Edit",
        "display_name": "Edit",
        "risk_level": "medium",
        "baseline_policy": "require_approval",
        "supports_approval": True,
    },
    {
        "tool_key": "Write",
        "display_name": "Write",
        "risk_level": "medium",
        "baseline_policy": "require_approval",
        "supports_approval": True,
    },
    {
        "tool_key": "Bash",
        "display_name": "Bash",
        "risk_level": "high",
        "baseline_policy": "require_approval",
        "supports_approval": True,
    },
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _load_skill_files(version: SkillVersion) -> list[dict[str, Any]]:
    storage: ArtifactStorage
    if settings.ARTIFACT_STORAGE_BACKEND == "local":
        storage = LocalArtifactStorage(Path(settings.ARTIFACT_LOCAL_ROOT))
    elif settings.ARTIFACT_STORAGE_BACKEND == "s3":
        from app.runtime.artifacts.s3_storage import S3ArtifactStorage

        if not settings.ARTIFACT_S3_BUCKET:
            raise ResolutionError(
                [
                    Diagnostic(
                        code="skill_storage_unavailable",
                        field="skills",
                        message="S3 Skill storage is not configured",
                    )
                ]
            )
        storage = S3ArtifactStorage(
            bucket=settings.ARTIFACT_S3_BUCKET,
            endpoint_url=settings.ARTIFACT_S3_ENDPOINT,
            access_key=settings.ARTIFACT_S3_ACCESS_KEY,
            secret_key=settings.ARTIFACT_S3_SECRET_KEY,
            region=settings.ARTIFACT_S3_REGION,
        )
    else:
        raise ResolutionError(
            [
                Diagnostic(
                    code="skill_storage_unavailable",
                    field="skills",
                    message="Skill storage backend is unsupported",
                )
            ]
        )
    stream = storage.open(version.storage_key)
    try:
        content = stream.read()
    finally:
        stream.close()
    if hashlib.sha256(content).hexdigest() != version.content_sha256:
        raise ResolutionError(
            [
                Diagnostic(
                    code="skill_content_digest_mismatch",
                    field="skills",
                    message="Stored Skill archive digest does not match its immutable version",
                )
            ]
        )
    files: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for entry in sorted(
            version.manifest.get("files", []), key=lambda item: item["path"]
        ):
            raw = archive.read(entry["path"])
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ResolutionError(
                    [
                        Diagnostic(
                            code="skill_file_digest_mismatch",
                            field="skills",
                            message=f"Stored Skill file digest changed: {entry['path']}",
                        )
                    ]
                )
            files.append(
                {
                    "path": entry["path"],
                    "sha256": entry["sha256"],
                    "size": len(raw),
                    "content_base64": base64.b64encode(raw).decode(),
                }
            )
    return files


def validate_semver(value: str) -> bool:
    match = SEMVER_PATTERN.fullmatch(value)
    if match is None:
        return False
    prerelease = match.group(4)
    if prerelease is None:
        return True
    return all(
        not (
            identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        )
        for identifier in prerelease.split(".")
    )


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value.replace("'", '"'))
            return parsed
        except json.JSONDecodeError:
            return [
                item.strip().strip("'\"")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
    if value.startswith("{") and value.endswith("}"):
        try:
            return json.loads(value.replace("'", '"'))
        except json.JSONDecodeError:
            return value
    return value.strip("'\"")


def parse_skill_frontmatter(text: str) -> dict[str, Any]:
    """Parse the constrained YAML frontmatter accepted by v0.5.

    This intentionally supports only flat scalars and lists. Complex executable
    directives are outside the declarative Skill schema and are rejected.
    """
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must begin with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("SKILL.md frontmatter is not terminated")
    result: dict[str, Any] = {}
    active_list: str | None = None
    for number, line in enumerate(text[4:end].splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and active_list:
            value = result.setdefault(active_list, [])
            if not isinstance(value, list):
                raise ValueError(f"frontmatter line {number} has an invalid list")
            value.append(_parse_scalar(line[4:]))
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise ValueError(
                f"frontmatter line {number} uses an unsupported nested value"
            )
        key, raw = line.split(":", 1)
        key = key.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError(f"frontmatter line {number} has an invalid key")
        if raw.strip():
            result[key] = _parse_scalar(raw)
            active_list = None
        else:
            result[key] = []
            active_list = key
    return result


class SkillScanResult(BaseModel):
    manifest: dict[str, Any]
    content_sha256: str
    size: int
    files: list[dict[str, Any]]
    diagnostics: list[Diagnostic] = Field(default_factory=list)


def scan_skill_archive(path: Path, *, slug: str, version: str) -> SkillScanResult:
    if not validate_semver(version):
        raise ValueError("version must be canonical SemVer")
    archive_digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            archive_digest.update(chunk)
    archive_hash = archive_digest.hexdigest()
    files: list[dict[str, Any]] = []
    total = 0
    skill_entry: bytes | None = None
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("Skill upload must be a valid ZIP archive") from exc
    with archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        if len(entries) > MAX_SKILL_FILES:
            raise ValueError("Skill archive contains too many files")
        entry_names = [item.filename for item in entries if item.filename == "SKILL.md"]
        if len(entry_names) != 1:
            raise ValueError("Skill archive must contain exactly one root SKILL.md")
        for info in entries:
            name = info.filename
            posix = PurePosixPath(name)
            windows = PureWindowsPath(name)
            if (
                "\\" in name
                or posix.is_absolute()
                or windows.is_absolute()
                or ".." in posix.parts
                or name.startswith("/")
            ):
                raise ValueError(f"unsafe archive path: {name}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"symbolic links are forbidden: {name}")
            if mode & 0o111:
                raise ValueError(f"executable files are forbidden: {name}")
            suffix = posix.suffix.lower()
            if (
                suffix in FORBIDDEN_SKILL_SUFFIXES
                or suffix not in ALLOWED_SKILL_SUFFIXES
            ):
                raise ValueError(f"unsupported Skill content type: {name}")
            if info.file_size > MAX_SKILL_FILE_BYTES:
                raise ValueError(f"Skill file exceeds size limit: {name}")
            total += info.file_size
            if total > MAX_SKILL_EXPANDED_BYTES:
                raise ValueError("Skill expanded size exceeds limit")
            content = archive.read(info)
            digest = hashlib.sha256(content).hexdigest()
            files.append({"path": name, "size": len(content), "sha256": digest})
            if name == "SKILL.md":
                skill_entry = content
            if suffix in {".md", ".txt", ".json", ".yaml", ".yml", ".svg"}:
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"text resource is not UTF-8: {name}") from exc
    assert skill_entry is not None
    manifest = parse_skill_frontmatter(skill_entry.decode("utf-8"))
    missing = sorted(SKILL_REQUIRED_FIELDS - manifest.keys())
    if missing:
        raise ValueError(f"SKILL.md frontmatter is missing: {', '.join(missing)}")
    if manifest["name"] != slug:
        raise ValueError("SKILL.md name must match the Skill slug")
    if manifest["version"] != version:
        raise ValueError("SKILL.md version must match the requested version")
    if manifest["invocation_mode"] not in {"discoverable", "explicit_user_message"}:
        raise ValueError("invocation_mode is not supported")
    for field in ("platforms", "required_tools", "required_mcp_tools", "content_types"):
        if not isinstance(manifest[field], list) or any(
            not isinstance(item, str) for item in manifest[field]
        ):
            raise ValueError(f"frontmatter {field} must be a string list")
    declared_types = set(manifest["content_types"])
    actual_types = {
        PurePosixPath(item["path"]).suffix.lower().lstrip(".") for item in files
    }
    if not actual_types.issubset(declared_types | {"md"}):
        raise ValueError("archive contains content types not declared in manifest")
    for forbidden in ("scripts", "hooks", "executable", "command", "install"):
        if forbidden in manifest:
            raise ValueError(f"executable manifest field is forbidden: {forbidden}")
    return SkillScanResult(
        manifest=manifest,
        content_sha256=archive_hash,
        size=path.stat().st_size,
        files=sorted(files, key=lambda item: item["path"]),
    )


def validate_mcp_config(
    transport: McpTransport, config: dict[str, Any], *, allow_http: bool = False
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    secret_keys = {
        "authorization",
        "token",
        "password",
        "secret",
        "api_key",
        "headers",
        "env",
    }
    for key in config:
        if key.lower() in secret_keys:
            diagnostics.append(
                Diagnostic(
                    code="secret_value_forbidden",
                    field=key,
                    message="MCP secrets must use a target secret handle",
                )
            )
    timeout_fields = {
        "connect_timeout_seconds": 30,
        "call_timeout_seconds": 300,
        "max_concurrency": 32,
        "max_result_bytes": 10 * 1024 * 1024,
        "idle_grace_seconds": 3600,
    }
    for key, maximum in timeout_fields.items():
        value = config.get(key)
        if value is not None and (
            not isinstance(value, int) or value <= 0 or value > maximum
        ):
            diagnostics.append(
                Diagnostic(
                    code="mcp_limit_invalid",
                    field=key,
                    message=f"{key} must be between 1 and {maximum}",
                )
            )
    if transport == McpTransport.STDIO:
        allowed = {"executable_key", "args", *timeout_fields}
        if not isinstance(config.get("executable_key"), str) or not re.fullmatch(
            r"[a-zA-Z0-9._-]+", str(config.get("executable_key", ""))
        ):
            diagnostics.append(
                Diagnostic(
                    code="invalid_executable_key",
                    field="executable_key",
                    message="stdio requires an inventory executable key",
                )
            )
        args = config.get("args", [])
        if not isinstance(args, list) or any(
            not isinstance(arg, str) or re.search(r"[;&|`$<>]", arg) for arg in args
        ):
            diagnostics.append(
                Diagnostic(
                    code="invalid_stdio_args",
                    field="args",
                    message="stdio args must be a shell-free string list",
                )
            )
    else:
        allowed = {"endpoint", *timeout_fields}
        endpoint = config.get("endpoint")
        try:
            parsed = urlparse(endpoint if isinstance(endpoint, str) else "")
            if (
                parsed.scheme not in ({"https", "http"} if allow_http else {"https"})
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError
            hostname = parsed.hostname.lower()
            if hostname in {"localhost", "localhost.localdomain"}:
                raise ValueError
            try:
                ip = ipaddress.ip_address(hostname.strip("[]"))
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    raise ValueError
            except ValueError as exc:
                if re.fullmatch(r"[0-9a-fA-F:.]+", hostname):
                    raise ValueError from exc
        except ValueError:
            diagnostics.append(
                Diagnostic(
                    code="unsafe_mcp_endpoint",
                    field="endpoint",
                    message="endpoint must be an allowed public HTTPS URL",
                )
            )
    for key in config:
        if key not in allowed:
            diagnostics.append(
                Diagnostic(
                    code="unknown_mcp_config_field",
                    field=key,
                    message=f"unsupported MCP config field: {key}",
                )
            )
    return diagnostics


def seed_builtin_tools(session: Session) -> None:
    for item in BUILTIN_TOOLS:
        existing = session.exec(
            select(ToolDefinition).where(
                ToolDefinition.harness_type == "claude_code",
                ToolDefinition.tool_key == item["tool_key"],
                ToolDefinition.adapter_schema_version == "1.0",
            )
        ).first()
        if existing is None:
            session.add(
                ToolDefinition(
                    harness_type="claude_code",
                    source=ToolSource.BUILTIN,
                    description=f"Claude built-in {item['display_name']} tool",
                    schema_digest=canonical_digest(
                        {"key": item["tool_key"], "schema": "claude-code-1.0"}
                    ),
                    **item,
                )
            )
    session.flush()


_POLICY_RANK = {
    "allow": 0,
    "inherit": 0,
    "require_approval": 1,
    "deny": 2,
    "disabled": 2,
    "forbidden": 3,
}


def effective_tool_policy(
    baseline: str, namespace: str | None, agent: str | None
) -> str:
    policies = [baseline, namespace or "inherit", agent or "inherit"]
    return max(policies, key=lambda value: _POLICY_RANK[value])


class ResolvedAgentSpec(BaseModel):
    schema_version: str = "1.1"
    agent_id: uuid.UUID
    agent_release_id: uuid.UUID | None = None
    harness_type: str | None = None
    harness_adapter_version: str | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    model: dict[str, Any]
    system_prompt: str
    policies: dict[str, Any]
    skills: list[dict[str, Any]]
    plugins: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    mcp_servers: list[dict[str, Any]]
    version_constraints: dict[str, str]
    required_capabilities: dict[str, Any]

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def digest(self) -> str:
        return canonical_digest(self.canonical())


class ResolutionError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = diagnostics
        super().__init__("Agent resolution failed")


def resolve_agent_spec(
    session: Session, agent: AgentDefinition, *, release_id: uuid.UUID | None = None
) -> tuple[ResolvedAgentSpec, dict[str, Any], list[dict[str, Any]]]:
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id)
    ).first()
    if draft is None:
        raise ResolutionError(
            [
                Diagnostic(
                    code="draft_missing",
                    field="agent",
                    message="Agent draft does not exist",
                )
            ]
        )
    diagnostics: list[Diagnostic] = []
    is_v09 = draft.preferred_model_definition_id is not None
    model_definition = (
        session.get(LlmModelDefinition, draft.preferred_model_definition_id)
        if draft.preferred_model_definition_id
        else None
    )
    profile = (
        session.get(HarnessProfile, draft.harness_profile_id)
        if draft.harness_profile_id
        else None
    )
    provider = (
        session.get(LlmProviderConfig, draft.provider_config_id)
        if draft.provider_config_id
        else None
    )
    if is_v09 and (
        model_definition is None
        or model_definition.namespace_id != agent.namespace_id
        or not model_definition.enabled
    ):
        diagnostics.append(
            Diagnostic(
                code="preferred_model_invalid",
                field="preferred_model_definition_id",
                message="Preferred model identity is missing, disabled, or cross-namespace",
            )
        )
    if not is_v09 and (
        profile is None
        or profile.namespace_id != agent.namespace_id
        or profile.archived
    ):
        diagnostics.append(
            Diagnostic(
                code="harness_invalid",
                field="harness_profile_id",
                message="Harness profile is missing, archived, or cross-namespace",
            )
        )
    elif not is_v09 and profile and profile.harness_type != "claude_code":
        diagnostics.append(
            Diagnostic(
                code="unsupported_harness",
                field="harness_type",
                message="v0.5 only supports claude_code",
            )
        )
    if not is_v09 and (
        provider is None
        or provider.namespace_id != agent.namespace_id
        or not provider.enabled
    ):
        diagnostics.append(
            Diagnostic(
                code="model_stale",
                field="provider_config_id",
                message="Model provider is unavailable",
            )
        )
    model = (
        session.exec(
            select(LlmProviderModel).where(
                LlmProviderModel.provider_config_id == draft.provider_config_id,
                LlmProviderModel.model_id == draft.model_id,
                col(LlmProviderModel.is_enabled).is_(True),
            )
        ).first()
        if provider and draft.model_id
        else None
    )
    if not is_v09 and model is None:
        diagnostics.append(
            Diagnostic(
                code="model_not_found",
                field="model_id",
                message="Selected model is unavailable",
            )
        )
    skill_rows = session.exec(
        select(AgentDraftSkill).where(AgentDraftSkill.agent_draft_id == draft.id)
    ).all()
    skills: list[dict[str, Any]] = []
    skill_index: dict[uuid.UUID, dict[str, Any]] = {}
    disabled_skill_ids = {row.skill_id for row in skill_rows if not row.enabled}
    required_tools: dict[str, list[str]] = {}
    required_mcp_tools: dict[str, list[str]] = {}

    def add_skill_identity(skill_identity: SkillDefinition | None, source: str) -> None:
        if (
            skill_identity is None
            or skill_identity.namespace_id != agent.namespace_id
            or skill_identity.archived
            or skill_identity.current_version_id is None
        ):
            diagnostics.append(
                Diagnostic(
                    code="skill_dependency_invalid",
                    field="skills",
                    message="Skill dependency is missing, archived, unpublished, or cross-namespace",
                )
            )
            return
        skill_version = session.get(SkillVersion, skill_identity.current_version_id)
        if (
            skill_version is None
            or skill_version.skill_id != skill_identity.id
            or skill_version.deprecated
        ):
            diagnostics.append(
                Diagnostic(
                    code="skill_current_version_invalid",
                    field="skills",
                    message=f"Skill {skill_identity.slug} has no usable current version",
                )
            )
            return
        existing = skill_index.get(skill_identity.id)
        if existing is not None:
            if source not in existing["sources"]:
                existing["sources"].append(source)
            return
        item = {
            "id": str(skill_identity.id),
            "slug": skill_identity.slug,
            "enabled": True,
            "sources": [source],
        }
        skill_index[skill_identity.id] = item
        skills.append(item)
        for tool in skill_version.required_capabilities.get("tools", []):
            required_tools.setdefault(tool, []).append(
                f"{source}/skill:{skill_identity.slug}"
            )
        for tool in skill_version.required_capabilities.get("mcp_tools", []):
            required_mcp_tools.setdefault(tool, []).append(
                f"{source}/skill:{skill_identity.slug}"
            )

    for skill_link in skill_rows:
        if skill_link.enabled:
            add_skill_identity(
                session.get(SkillDefinition, skill_link.skill_id), "agent"
            )
    plugin_rows = session.exec(
        select(AgentDraftPlugin).where(AgentDraftPlugin.agent_draft_id == draft.id)
    ).all()
    plugins: list[dict[str, Any]] = []
    plugin_mcp_components: list[tuple[dict[str, Any], str]] = []
    plugin_tool_policies: dict[str, list[tuple[str, str]]] = {}
    plugin_config_fragments: list[dict[str, Any]] = []
    for plugin_link in plugin_rows:
        plugin_identity = session.get(Plugin, plugin_link.plugin_id)
        plugin_version = session.get(PluginVersion, plugin_link.plugin_version_id)
        if (
            plugin_identity is None
            or plugin_version is None
            or plugin_identity.namespace_id != agent.namespace_id
            or plugin_version.plugin_id != plugin_identity.id
            or plugin_version.deprecated
        ):
            diagnostics.append(
                Diagnostic(
                    code="plugin_dependency_invalid",
                    field="plugins",
                    message="Plugin dependency is missing, deprecated, or cross-namespace",
                )
            )
            continue
        if (
            not is_v09
            and profile
            and plugin_version.harness_type != profile.harness_type
        ):
            diagnostics.append(
                Diagnostic(
                    code="plugin_harness_mismatch",
                    field="plugins",
                    message=f"Plugin {plugin_identity.slug} uses a different harness",
                )
            )
        for component in plugin_version.manifest.get("provides", []):
            if component.get("type") == "skill":
                skill_identity: SkillDefinition | None = None
                try:
                    if component.get("skill_id"):
                        skill_identity = session.get(
                            SkillDefinition, uuid.UUID(str(component["skill_id"]))
                        )
                    elif component.get("skill_version_id"):
                        legacy_version = session.get(
                            SkillVersion,
                            uuid.UUID(str(component["skill_version_id"])),
                        )
                        if legacy_version:
                            skill_identity = session.get(
                                SkillDefinition, legacy_version.skill_id
                            )
                except (TypeError, ValueError):
                    skill_identity = None
                if skill_identity is None:
                    diagnostics.append(
                        Diagnostic(
                            code="plugin_skill_invalid",
                            field="plugins",
                            message=f"Plugin {plugin_identity.slug} contains an unavailable Skill identity",
                        )
                    )
                    continue
                if skill_identity.id in disabled_skill_ids:
                    continue
                add_skill_identity(
                    skill_identity,
                    f"plugin:{plugin_identity.slug}@{plugin_version.version}",
                )
            elif component.get("type") == "mcp_server":
                plugin_mcp_components.append(
                    (
                        component,
                        f"plugin:{plugin_identity.slug}@{plugin_version.version}",
                    )
                )
            elif component.get("type") == "tool_policy":
                key = str(component.get("tool_key"))
                plugin_tool_policies.setdefault(key, []).append(
                    (
                        str(component.get("policy")),
                        f"plugin:{plugin_identity.slug}@{plugin_version.version}",
                    )
                )
            elif component.get("type") == "harness_config_fragment":
                if is_v09:
                    diagnostics.append(
                        Diagnostic(
                            code="legacy_harness_plugin_component",
                            field="plugins",
                            message=f"Plugin {plugin_identity.slug} contributes deprecated Harness configuration",
                        )
                    )
                else:
                    plugin_config_fragments.append(
                        {
                            "source": f"plugin:{plugin_identity.slug}@{plugin_version.version}",
                            "config": component.get("config", {}),
                        }
                    )
        plugins.append(
            {
                "id": str(plugin_identity.id),
                "slug": plugin_identity.slug,
                "version_id": str(plugin_version.id),
                "version": plugin_version.version,
                "manifest_digest": plugin_version.manifest_digest,
                "provides": plugin_version.manifest.get("provides", []),
            }
        )
    adapter_config = (
        {} if is_v09 else {**(profile.config if profile else {}), **draft.config}
    )
    for contribution in plugin_config_fragments:
        source = contribution["source"]
        fragment = contribution["config"]
        for key, value in fragment.items():
            current = adapter_config.get(key)
            if current is None or current == value:
                adapter_config[key] = value
            elif key == "timeout_seconds":
                adapter_config[key] = min(int(current), int(value))
            elif key in {"disallowed_tools"}:
                adapter_config[key] = sorted(set(current) | set(value))
            elif key in {"allowed_tools", "allowed_env_names"}:
                adapter_config[key] = sorted(set(current) & set(value))
            else:
                diagnostics.append(
                    Diagnostic(
                        code="plugin_config_conflict",
                        field=f"plugins.{key}",
                        message=f"{source} conflicts with the effective Agent/Harness setting {key}",
                    )
                )
    seed_builtin_tools(session)
    tool_defs = session.exec(
        select(ToolDefinition).where(ToolDefinition.harness_type == "claude_code")
    ).all()
    namespace_policies = {
        str(row.tool_definition_id): row.policy.value
        for row in session.exec(
            select(NamespaceToolPolicy).where(
                NamespaceToolPolicy.namespace_id == agent.namespace_id
            )
        ).all()
    }
    agent_policies = {
        row.tool_key: row.policy.value
        for row in session.exec(
            select(AgentDraftToolPolicy).where(
                AgentDraftToolPolicy.agent_draft_id == draft.id
            )
        ).all()
    }
    for key, intents in plugin_tool_policies.items():
        distinct = {policy for policy, _source in intents}
        if key in agent_policies and agent_policies[key] != "inherit":
            distinct.add(agent_policies[key])
        if len(distinct) > 1:
            policy_sources = ", ".join(source for _policy, source in intents)
            diagnostics.append(
                Diagnostic(
                    code="tool_policy_conflict",
                    field="tools",
                    message=f"Tool {key} has conflicting policy intents from {policy_sources} and the Agent draft",
                )
            )
        agent_policies[key] = max(
            (policy for policy, _source in intents),
            key=lambda value: _POLICY_RANK[value],
        )
    tools: list[dict[str, Any]] = []
    known_tools = {tool.tool_key for tool in tool_defs}
    for tool in tool_defs:
        policy = effective_tool_policy(
            tool.baseline_policy.value,
            namespace_policies.get(str(tool.id)),
            agent_policies.get(tool.tool_key),
        )
        if (
            tool.tool_key in set(adapter_config.get("disallowed_tools", []))
            or adapter_config.get("allowed_tools")
            and tool.tool_key not in set(adapter_config["allowed_tools"])
        ):
            policy = effective_tool_policy(policy, "disabled", None)
        tools.append(
            {
                "key": tool.tool_key,
                "source": tool.source.value,
                "schema_digest": tool.schema_digest,
                "risk_level": tool.risk_level,
                "policy": policy,
            }
        )
    for required, requirement_sources in required_tools.items():
        resolved = next((item for item in tools if item["key"] == required), None)
        if required not in known_tools:
            diagnostics.append(
                Diagnostic(
                    code="required_tool_unknown",
                    field="tools",
                    message=f"Required Tool {required} from {', '.join(requirement_sources)} is unknown",
                )
            )
        elif resolved and resolved["policy"] in {"deny", "disabled", "forbidden"}:
            diagnostics.append(
                Diagnostic(
                    code="required_tool_forbidden",
                    field="tools",
                    message=f"Required Tool {required} from {', '.join(requirement_sources)} is forbidden",
                )
            )
    mcp_rows = session.exec(
        select(AgentDraftMcp).where(AgentDraftMcp.agent_draft_id == draft.id)
    ).all()
    mcp_contributions: list[tuple[uuid.UUID, uuid.UUID, list[str], str]] = [
        (row.server_id, row.revision_id, row.allowed_tools, "agent") for row in mcp_rows
    ]
    for component, source in plugin_mcp_components:
        try:
            mcp_contributions.append(
                (
                    uuid.UUID(str(component["server_id"])),
                    uuid.UUID(str(component["revision_id"])),
                    list(component.get("tool_allowlist", [])),
                    source,
                )
            )
        except (KeyError, ValueError):
            diagnostics.append(
                Diagnostic(
                    code="plugin_mcp_invalid",
                    field="plugins",
                    message=f"{source} contains an invalid MCP contribution",
                )
            )
    mcp_servers: list[dict[str, Any]] = []
    seen_mcp: dict[uuid.UUID, tuple[uuid.UUID, list[str], str]] = {}
    exposed_mcp_tools: set[str] = set()
    verified_tool_snapshots: dict[str, McpToolSnapshot] = {}
    for server_id, revision_id, allowed_tools, source in mcp_contributions:
        previous = seen_mcp.get(server_id)
        if previous and (
            previous[0] != revision_id or previous[1] != sorted(allowed_tools)
        ):
            diagnostics.append(
                Diagnostic(
                    code="mcp_dependency_conflict",
                    field="mcp",
                    message=f"MCP Server is contributed differently by {previous[2]} and {source}",
                )
            )
            continue
        if previous:
            continue
        seen_mcp[server_id] = (revision_id, sorted(allowed_tools), source)
        server = session.get(McpServer, server_id)
        revision = session.get(McpServerRevision, revision_id)
        if (
            server is None
            or revision is None
            or server.namespace_id != agent.namespace_id
            or revision.server_id != server.id
            or revision.deprecated
        ):
            diagnostics.append(
                Diagnostic(
                    code="mcp_dependency_invalid",
                    field="mcp",
                    message="MCP dependency is missing, deprecated, or cross-namespace",
                )
            )
            continue
        targets = session.exec(
            select(McpTargetBinding).where(
                McpTargetBinding.revision_id == revision.id,
                McpTargetBinding.status == McpTargetStatus.VERIFIED,
            )
        ).all()
        if not targets:
            diagnostics.append(
                Diagnostic(
                    code="mcp_target_unverified",
                    field="mcp",
                    message=f"MCP {server.slug} has no verified target",
                )
            )
        digests = sorted(
            {target.tool_digest for target in targets if target.tool_digest}
        )
        if targets:
            revision_snapshots = session.exec(
                select(McpToolSnapshot)
                .join(
                    McpValidationAttempt,
                    col(McpToolSnapshot.validation_attempt_id)
                    == McpValidationAttempt.id,
                )
                .where(
                    col(McpValidationAttempt.target_binding_id).in_(
                        [target.id for target in targets]
                    ),
                    McpValidationAttempt.status == McpTargetStatus.VERIFIED,
                    col(McpToolSnapshot.qualified_name).in_(allowed_tools),
                )
                .order_by(col(McpValidationAttempt.completed_at).desc())
            ).all()
            for revision_snapshot in revision_snapshots:
                verified_tool_snapshots.setdefault(
                    revision_snapshot.qualified_name, revision_snapshot
                )
        exposed_mcp_tools.update(allowed_tools)
        mcp_servers.append(
            {
                "id": str(server.id),
                "slug": server.slug,
                "revision_id": str(revision.id),
                "revision": revision.revision,
                "transport": revision.transport.value,
                "config": revision.config,
                "config_sha256": revision.config_sha256,
                "allowed_tools": sorted(allowed_tools),
                "tool_digests": digests,
                "secret_handles": [
                    {
                        "runtime_profile_id": str(target.runtime_profile_id),
                        "secret_ref": target.secret_ref,
                        "platform_secret_record": target.secret_ref is None,
                    }
                    for target in targets
                ],
            }
        )
    for qualified_name in sorted(exposed_mcp_tools):
        tool_snapshot = verified_tool_snapshots.get(qualified_name)
        if tool_snapshot is None:
            diagnostics.append(
                Diagnostic(
                    code="mcp_tool_snapshot_missing",
                    field="mcp",
                    message=f"Allowed MCP Tool {qualified_name} has no immutable validation snapshot",
                )
            )
            continue
        policy = effective_tool_policy(
            "require_approval", None, agent_policies.get(qualified_name)
        )
        tools.append(
            {
                "key": qualified_name,
                "source": "mcp",
                "schema_digest": tool_snapshot.schema_digest,
                "risk_level": "external",
                "policy": policy,
            }
        )
    for key, intents in plugin_tool_policies.items():
        if key not in known_tools and key not in exposed_mcp_tools:
            diagnostics.append(
                Diagnostic(
                    code="plugin_tool_unknown",
                    field="plugins",
                    message=f"Plugin Tool policy references unavailable Tool {key} from {', '.join(source for _policy, source in intents)}",
                )
            )
    for required, requirement_sources in required_mcp_tools.items():
        if required not in exposed_mcp_tools:
            diagnostics.append(
                Diagnostic(
                    code="required_mcp_tool_missing",
                    field="mcp",
                    message=f"Required MCP Tool {required} from {', '.join(sorted(set(requirement_sources)))} is not exposed by an exact verified MCP binding",
                )
            )
        elif next((item for item in tools if item["key"] == required), {}).get(
            "policy"
        ) in {"deny", "disabled", "forbidden"}:
            diagnostics.append(
                Diagnostic(
                    code="required_mcp_tool_forbidden",
                    field="mcp",
                    message=f"Required MCP Tool {required} from {', '.join(sorted(set(requirement_sources)))} is forbidden by effective policy",
                )
            )
    if diagnostics:
        raise ResolutionError(diagnostics)
    if is_v09:
        assert model_definition is not None
        harness_type = None
        resolved_model: dict[str, Any] = {
            "preferred_model_definition_id": str(model_definition.id),
            "provider_family": model_definition.provider_family,
            "model_key": model_definition.model_key,
        }
        version_constraints: dict[str, str] = {}
    else:
        assert profile is not None and provider is not None
        harness_type = profile.harness_type
        resolved_model = {
            "provider_config_id": str(provider.id),
            "model_id": draft.model_id,
        }
        version_constraints = {
            "cli": profile.cli_version_constraint,
            "sdk": profile.sdk_version_constraint,
        }
    profile_diags = validate_config(profile.config, is_profile=True) if profile else []
    draft_diags = [] if is_v09 else validate_config(draft.config, is_profile=False)
    if profile_diags or draft_diags:
        raise ResolutionError(profile_diags + draft_diags)
    timeout = adapter_config.get("timeout_seconds", MAX_TIMEOUT_SECONDS)
    spec = ResolvedAgentSpec(
        schema_version="2.0" if is_v09 else "1.1",
        agent_id=agent.id,
        agent_release_id=release_id,
        harness_type=harness_type,
        harness_adapter_version=None if is_v09 else "claude-code-1.1",
        adapter_config=adapter_config,
        model=resolved_model,
        system_prompt=draft.system_prompt,
        policies=(
            draft.execution_policy
            if is_v09
            else {
                "permission_mode": adapter_config.get("permission_mode", "default"),
                "timeout_seconds": timeout,
                "working_directory_strategy": adapter_config.get(
                    "working_directory_strategy", "inherit"
                ),
            }
        ),
        skills=sorted(skills, key=lambda item: item["slug"]),
        plugins=sorted(plugins, key=lambda item: item["slug"]),
        tools=sorted(tools, key=lambda item: item["key"]),
        mcp_servers=sorted(mcp_servers, key=lambda item: item["slug"]),
        version_constraints=version_constraints,
        required_capabilities={
            "tools": sorted(required_tools),
            "mcp_tools": sorted(required_mcp_tools),
            **(
                {}
                if is_v09
                else {
                    "harness": "claude_code",
                    "plugin_config_fragments": plugin_config_fragments,
                }
            ),
        },
    )
    dependency_lock = {
        "skills": [
            {
                "id": item["id"],
                "slug": item["slug"],
                "sources": item["sources"],
            }
            for item in spec.skills
        ],
        "plugins": [
            {
                "slug": item["slug"],
                "version": item["version"],
                "digest": item["manifest_digest"],
            }
            for item in spec.plugins
        ],
        "mcp": [
            {
                "slug": item["slug"],
                "revision": item["revision"],
                "digest": item["config_sha256"],
                "tool_digests": item["tool_digests"],
            }
            for item in spec.mcp_servers
        ],
        "tools": [
            {
                "key": item["key"],
                "schema_digest": item["schema_digest"],
                "policy": item["policy"],
            }
            for item in spec.tools
        ],
    }
    components = [
        {"type": kind, **item}
        for kind, values in (
            ("skill", dependency_lock["skills"]),
            ("plugin", dependency_lock["plugins"]),
            ("mcp", dependency_lock["mcp"]),
            ("tool", dependency_lock["tools"]),
        )
        for item in values
    ]
    return spec, dependency_lock, components


class ClaudeCodeHarnessAdapter:
    schema_version = "1.1"
    adapter_version = "claude-code-1.1"

    def validate_spec(self, spec: ResolvedAgentSpec) -> list[Diagnostic]:
        if spec.harness_type != "claude_code":
            return [
                Diagnostic(
                    code="unsupported_harness",
                    field="harness_type",
                    message="Claude adapter only accepts claude_code",
                )
            ]
        if spec.policies.get("permission_mode") not in {
            "default",
            "acceptEdits",
            "plan",
        }:
            return [
                Diagnostic(
                    code="invalid_permission_mode",
                    field="policies.permission_mode",
                    message="Unsupported Claude permission mode",
                )
            ]
        return []

    def validate_target(
        self,
        spec: ResolvedAgentSpec,
        runtime: RuntimeProfile,
        capability_inventory: dict[str, Any] | None = None,
    ) -> list[Diagnostic]:
        inventory = capability_inventory or runtime.harness_capabilities
        capability = inventory.get("claude_code", {})
        diagnostics: list[Diagnostic] = []
        if (
            str(runtime.provider_config_id) != spec.model["provider_config_id"]
            or runtime.model_id != spec.model["model_id"]
        ):
            diagnostics.append(
                Diagnostic(
                    code="target_model_route_mismatch",
                    field="model",
                    message="Target runtime model route does not exactly match the Agent Release",
                )
            )
        for field in ("cli_version", "sdk_version", "harness_version"):
            if not capability.get(field):
                diagnostics.append(
                    Diagnostic(
                        code="target_capability_unknown",
                        field=f"harness_capabilities.claude_code.{field}",
                        message=f"Target did not report {field}",
                    )
                )
        available_tools = set(
            capability.get("builtin_tools", [item["key"] for item in spec.tools])
        )
        for item in spec.tools:
            if (
                item.get("source") == "builtin"
                and item["policy"] not in {"deny", "disabled", "forbidden"}
                and item["key"] not in available_tools
            ):
                diagnostics.append(
                    Diagnostic(
                        code="target_tool_unavailable",
                        field="tools",
                        message=f"Target does not provide Tool {item['key']}",
                    )
                )
        return diagnostics

    def materialization_manifest(self, spec: ResolvedAgentSpec) -> dict[str, Any]:
        options = self.build_execution_options(spec)
        return {
            "schema_version": self.schema_version,
            "adapter_version": self.adapter_version,
            "files": [],
            "skill_identities": [
                {"id": item["id"], "slug": item["slug"]} for item in spec.skills
            ],
            "mcp_config_digest": canonical_digest(spec.mcp_servers),
            "tool_schema_digest": canonical_digest(spec.tools),
            "options_digest": canonical_digest(options),
            "resolved_spec_digest": spec.digest(),
        }

    def build_execution_options(self, spec: ResolvedAgentSpec) -> dict[str, Any]:
        allowed = [
            item["key"]
            for item in spec.tools
            if item["policy"] in {"allow", "require_approval"}
        ]
        disallowed = [
            item["key"]
            for item in spec.tools
            if item["policy"] in {"deny", "disabled", "forbidden"}
        ]
        return {
            "model": spec.model["model_id"],
            "system_prompt": spec.system_prompt,
            "permission_mode": spec.policies["permission_mode"],
            "allowed_tools": sorted(allowed),
            "disallowed_tools": sorted(disallowed),
            "timeout_seconds": spec.policies["timeout_seconds"],
            "mcp_servers": [
                {
                    "name": item["slug"],
                    "transport": item["transport"],
                    "config": item["config"],
                }
                for item in spec.mcp_servers
            ],
        }


def runtime_capability_fingerprint(
    runtime: RuntimeProfile, capability_inventory: dict[str, Any] | None = None
) -> str:
    return canonical_digest(
        {
            "runtime_type": runtime.runtime_type.value,
            "harness_capabilities": capability_inventory
            or runtime.harness_capabilities,
            "config": {
                "allowed_working_roots": runtime.config.get("allowed_working_roots", [])
            },
        }
    )
