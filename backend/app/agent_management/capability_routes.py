"""Namespace management APIs for Skills, Tools, MCP servers, and Plugins."""

import hashlib
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.agent_management.capabilities import (
    canonical_bytes,
    canonical_digest,
    runtime_capability_fingerprint,
    scan_skill_archive,
    seed_builtin_tools,
    validate_mcp_config,
    validate_semver,
)
from app.agent_management.capability_models import (
    AgentDraftMcp,
    AgentDraftPlugin,
    AgentDraftSkill,
    AgentDraftToolPolicy,
    McpPlatformSecret,
    McpRuntimeEvent,
    McpRuntimeInstance,
    McpRuntimeStatus,
    McpServer,
    McpServerRevision,
    McpTargetBinding,
    McpTargetStatus,
    McpToolSnapshot,
    McpTransport,
    McpValidationAttempt,
    NamespaceToolPolicy,
    Plugin,
    PluginDraft,
    PluginVersion,
    SkillDefinition,
    SkillVersion,
    ToolBaseline,
    ToolDefinition,
    ToolPolicy,
)
from app.agent_management.catalog import (
    ENV_DENYLIST,
    ENV_DENYLIST_PREFIXES,
    validate_config,
)
from app.agent_management.models import AgentDefinition, AgentDraft
from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.core.config import settings
from app.llm_provider_service import open_secret_payload, seal_secret_payload
from app.runtime.artifacts.local_storage import LocalArtifactStorage
from app.runtime.artifacts.s3_storage import S3ArtifactStorage
from app.runtime.artifacts.signing import configured_artifact_signer
from app.runtime.artifacts.storage import ArtifactStorage
from app.runtime.models import RuntimeProfile, RuntimeType
from app.runtime.security import require_internal_runtime

router = APIRouter(tags=["agent-capabilities"])
node_router = APIRouter(
    prefix="/node",
    tags=["node-mcp"],
    dependencies=[Depends(require_internal_runtime)],
)
mcp_internal_router = APIRouter(
    prefix="/internal/runtime",
    tags=["internal-mcp"],
    dependencies=[Depends(require_internal_runtime)],
)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityCreate(StrictBody):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class IdentityUpdate(StrictBody):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    archived: bool | None = None


class ExactBinding(StrictBody):
    id: uuid.UUID


class AgentSkillBinding(StrictBody):
    skill_version_id: uuid.UUID


class AgentSkillBindings(StrictBody):
    expected_revision: int
    skills: list[AgentSkillBinding]


class ToolIntent(StrictBody):
    tool_key: str = Field(min_length=1, max_length=512)
    policy: ToolPolicy


class AgentToolBindings(StrictBody):
    expected_revision: int
    tools: list[ToolIntent]


class McpRevisionCreate(StrictBody):
    transport: McpTransport
    config: dict[str, Any]
    protocol_version: str = Field(default="2025-06-18", max_length=64)


class McpTargetCreate(StrictBody):
    runtime_profile_id: uuid.UUID
    secret_ref: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9._/-]+$"
    )


class McpSecretWrite(StrictBody):
    secret_inputs: dict[str, str]


class McpCompleteCreate(IdentityCreate):
    revision: McpRevisionCreate
    target: McpTargetCreate
    secret_inputs: dict[str, str] = Field(default_factory=dict)


class McpAgentBinding(StrictBody):
    revision_id: uuid.UUID
    allowed_tools: list[str] = Field(default_factory=list)


class AgentMcpBindings(StrictBody):
    expected_revision: int
    mcp: list[McpAgentBinding]


class McpValidationResult(StrictBody):
    status: str
    capability_fingerprint: str
    secret_fingerprint: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class PluginDraftSave(StrictBody):
    expected_revision: int
    harness_type: str = "claude_code"
    adapter_schema_version: str = "1.0"
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)


class PluginCompleteCreate(IdentityCreate):
    harness_type: str = "claude_code"
    adapter_schema_version: str = "1.0"
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(min_length=1)


class PluginVersionCreate(StrictBody):
    draft_revision: int
    version: str


class AgentPluginBinding(StrictBody):
    plugin_version_id: uuid.UUID


class AgentPluginBindings(StrictBody):
    expected_revision: int
    plugins: list[AgentPluginBinding]


def _get_agent_draft(
    session: SessionDep,
    namespace_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    for_update: bool = True,
) -> tuple[AgentDefinition, AgentDraft]:
    agent = session.get(AgentDefinition, agent_id)
    if agent is None or agent.namespace_id != namespace_id:
        raise HTTPException(404, "Agent not found")
    statement = select(AgentDraft).where(AgentDraft.agent_id == agent.id)
    if for_update:
        statement = statement.with_for_update()
    draft = session.exec(statement).first()
    if draft is None:
        raise HTTPException(404, "Agent draft not found")
    return agent, draft


def _advance_draft(draft: AgentDraft, expected_revision: int) -> None:
    if draft.revision != expected_revision:
        raise HTTPException(
            409, {"code": "draft_revision_conflict", "current_revision": draft.revision}
        )
    draft.revision += 1
    draft.validated_revision = None
    draft.validation_result = None
    draft.updated_at = datetime.now(timezone.utc)


@router.get("/agents/{agent_id}/draft/capabilities")
def get_agent_capabilities(
    agent_id: uuid.UUID,
    session: SessionDep,
    _current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    _agent, draft = _get_agent_draft(session, namespace_id, agent_id, for_update=False)
    skills = session.exec(
        select(AgentDraftSkill).where(AgentDraftSkill.agent_draft_id == draft.id)
    ).all()
    tools = session.exec(
        select(AgentDraftToolPolicy).where(
            AgentDraftToolPolicy.agent_draft_id == draft.id
        )
    ).all()
    mcp = session.exec(
        select(AgentDraftMcp).where(AgentDraftMcp.agent_draft_id == draft.id)
    ).all()
    plugins = session.exec(
        select(AgentDraftPlugin).where(AgentDraftPlugin.agent_draft_id == draft.id)
    ).all()
    return {
        "revision": draft.revision,
        "skills": [
            {"skill_id": row.skill_id, "skill_version_id": row.skill_version_id}
            for row in skills
        ],
        "tools": [
            {"tool_key": row.tool_key, "policy": row.policy.value} for row in tools
        ],
        "mcp": [
            {
                "server_id": row.server_id,
                "revision_id": row.revision_id,
                "allowed_tools": row.allowed_tools,
            }
            for row in mcp
        ],
        "plugins": [
            {"plugin_id": row.plugin_id, "plugin_version_id": row.plugin_version_id}
            for row in plugins
        ],
    }


def _identity_public(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "namespace_id": value.namespace_id,
        "slug": value.slug,
        "name": value.name,
        "description": value.description,
        "archived": value.archived,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def _skill_version_public(value: SkillVersion) -> dict[str, Any]:
    return {
        "id": value.id,
        "skill_id": value.skill_id,
        "version": value.version,
        "content_sha256": value.content_sha256,
        "size": value.size,
        "manifest": value.manifest,
        "invocation_mode": value.invocation_mode,
        "platforms": value.platforms,
        "required_capabilities": value.required_capabilities,
        "content_types": value.content_types,
        "validation_result": value.validation_result,
        "deprecated": value.deprecated,
        "created_at": value.created_at,
    }


def _artifact_storage() -> ArtifactStorage:
    if settings.ARTIFACT_STORAGE_BACKEND == "local":
        return LocalArtifactStorage(Path(settings.ARTIFACT_LOCAL_ROOT))
    if settings.ARTIFACT_STORAGE_BACKEND == "s3" and settings.ARTIFACT_S3_BUCKET:
        return S3ArtifactStorage(
            bucket=settings.ARTIFACT_S3_BUCKET,
            endpoint_url=settings.ARTIFACT_S3_ENDPOINT,
            access_key=settings.ARTIFACT_S3_ACCESS_KEY,
            secret_key=settings.ARTIFACT_S3_SECRET_KEY,
            region=settings.ARTIFACT_S3_REGION,
        )
    raise HTTPException(503, "Configured immutable Skill storage is unavailable")


@router.get("/skills")
def list_skills(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    rows = session.exec(
        select(SkillDefinition)
        .where(SkillDefinition.namespace_id == namespace_id)
        .order_by(col(SkillDefinition.created_at).desc())
    ).all()
    return {"data": [_identity_public(row) for row in rows], "count": len(rows)}


@router.post("/skills", status_code=201)
def create_skill(
    body: IdentityCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    row = SkillDefinition(
        namespace_id=namespace_id, created_by=current_user.id, **body.model_dump()
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Skill slug already exists") from exc
    return _identity_public(row)


@router.post("/skills/complete", status_code=201)
async def create_skill_complete(
    session: SessionDep,
    current_user: CurrentUser,
    slug: str = Form(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$"),
    name: str = Form(min_length=1, max_length=255),
    description: str | None = Form(default=None),
    version: str = Form(),
    file: UploadFile = File(),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    temp_root = Path(settings.ARTIFACT_TEMP_DIR or tempfile.gettempdir())
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="neomua-skill-", suffix=".zip", dir=temp_root
    )
    temporary = Path(temporary_name)
    try:
        size = 0
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.ARTIFACT_MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "Skill archive exceeds size limit")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        try:
            scan = scan_skill_archive(temporary, slug=slug, version=version)
        except ValueError as exc:
            raise HTTPException(
                422, {"code": "invalid_skill_archive", "message": str(exc)}
            ) from exc
        storage_key = f"skills/sha256/{scan.content_sha256[:2]}/{scan.content_sha256}"
        try:
            _artifact_storage().put_once(storage_key, temporary)
        except OSError as exc:
            if exc.errno == 28:
                raise HTTPException(
                    507, "Skill storage capacity is insufficient"
                ) from exc
            raise
        skill = SkillDefinition(
            namespace_id=namespace_id,
            slug=slug,
            name=name,
            description=description,
            created_by=current_user.id,
        )
        manifest = {**scan.manifest, "files": scan.files}
        skill_version = SkillVersion(
            skill_id=skill.id,
            version=version,
            content_sha256=scan.content_sha256,
            storage_key=storage_key,
            size=scan.size,
            manifest=manifest,
            invocation_mode=scan.manifest["invocation_mode"],
            platforms=scan.manifest["platforms"],
            required_capabilities={
                "tools": scan.manifest["required_tools"],
                "mcp_tools": scan.manifest["required_mcp_tools"],
                "config_schema": scan.manifest["config_schema"],
            },
            content_types=scan.manifest["content_types"],
            validation_result={"status": "validated", "diagnostics": []},
            created_by=current_user.id,
        )
        session.add(skill)
        session.add(skill_version)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                409, "Skill identifier, version, or content already exists"
            ) from exc
        return {
            "skill": _identity_public(skill),
            "version": _skill_version_public(skill_version),
        }
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


def _get_skill(
    session: SessionDep, skill_id: uuid.UUID, namespace_id: uuid.UUID
) -> SkillDefinition:
    value = session.get(SkillDefinition, skill_id)
    if value is None or value.namespace_id != namespace_id:
        raise HTTPException(404, "Skill not found")
    return value


@router.get("/skills/{skill_id}")
def get_skill(
    skill_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    value = _get_skill(session, skill_id, namespace_id)
    versions = session.exec(
        select(SkillVersion)
        .where(SkillVersion.skill_id == value.id)
        .order_by(col(SkillVersion.created_at).desc())
    ).all()
    return {
        **_identity_public(value),
        "versions": [_skill_version_public(item) for item in versions],
    }


@router.patch("/skills/{skill_id}")
def update_skill(
    skill_id: uuid.UUID,
    body: IdentityUpdate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    value = _get_skill(session, skill_id, namespace_id)
    for field, item in body.model_dump(exclude_unset=True).items():
        setattr(value, field, item)
    value.updated_at = datetime.now(timezone.utc)
    session.add(value)
    session.commit()
    return _identity_public(value)


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(
    skill_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    value = _get_skill(session, skill_id, namespace_id)
    version_ids = session.exec(
        select(SkillVersion.id).where(SkillVersion.skill_id == value.id)
    ).all()
    referenced = (
        session.exec(
            select(AgentDraftSkill).where(
                col(AgentDraftSkill.skill_version_id).in_(version_ids)
            )
        ).first()
        if version_ids
        else None
    )
    if referenced:
        raise HTTPException(409, "Referenced Skill can only be archived")
    session.delete(value)
    session.commit()


@router.post("/skills/{skill_id}/versions", status_code=201)
async def upload_skill_version(
    skill_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    version: str = Form(),
    file: UploadFile = File(),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    skill = _get_skill(session, skill_id, namespace_id)
    if skill.archived:
        raise HTTPException(409, "Archived Skill cannot receive versions")
    temp_root = Path(settings.ARTIFACT_TEMP_DIR or tempfile.gettempdir())
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(
        prefix="neomua-skill-", suffix=".zip", dir=temp_root
    )
    temporary = Path(name)
    try:
        size = 0
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.ARTIFACT_MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "Skill archive exceeds size limit")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        try:
            scan = scan_skill_archive(temporary, slug=skill.slug, version=version)
        except ValueError as exc:
            raise HTTPException(
                422, {"code": "invalid_skill_archive", "message": str(exc)}
            ) from exc
        existing = session.exec(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill.id, SkillVersion.version == version
            )
        ).first()
        if existing:
            if existing.content_sha256 != scan.content_sha256:
                raise HTTPException(
                    409, "Skill version already exists with different content"
                )
            return _skill_version_public(existing)
        storage_key = f"skills/sha256/{scan.content_sha256[:2]}/{scan.content_sha256}"
        try:
            _artifact_storage().put_once(storage_key, temporary)
        except OSError as exc:
            if exc.errno == 28:
                raise HTTPException(
                    507, "Skill storage capacity is insufficient"
                ) from exc
            raise
        manifest = {**scan.manifest, "files": scan.files}
        row = SkillVersion(
            skill_id=skill.id,
            version=version,
            content_sha256=scan.content_sha256,
            storage_key=storage_key,
            size=scan.size,
            manifest=manifest,
            invocation_mode=scan.manifest["invocation_mode"],
            platforms=scan.manifest["platforms"],
            required_capabilities={
                "tools": scan.manifest["required_tools"],
                "mcp_tools": scan.manifest["required_mcp_tools"],
                "config_schema": scan.manifest["config_schema"],
            },
            content_types=scan.manifest["content_types"],
            validation_result={"status": "validated", "diagnostics": []},
            created_by=current_user.id,
        )
        session.add(row)
        session.commit()
        return _skill_version_public(row)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


@router.get("/skills/{skill_id}/versions")
def list_skill_versions(
    skill_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    skill = _get_skill(session, skill_id, namespace_id)
    rows = session.exec(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill.id)
        .order_by(col(SkillVersion.created_at).desc())
    ).all()
    return {"data": [_skill_version_public(row) for row in rows], "count": len(rows)}


@router.get("/skills/{skill_id}/versions/{version}")
def get_skill_version(
    skill_id: uuid.UUID,
    version: str,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    skill = _get_skill(session, skill_id, namespace_id)
    row = session.exec(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id, SkillVersion.version == version
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Skill version not found")
    return _skill_version_public(row)


@router.post("/skills/{skill_id}/versions/{version}/deprecate")
def deprecate_skill_version(
    skill_id: uuid.UUID,
    version: str,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    skill = _get_skill(session, skill_id, namespace_id)
    row = session.exec(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id, SkillVersion.version == version
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Skill version not found")
    row.deprecated = True
    session.add(row)
    session.commit()
    return _skill_version_public(row)


@router.get("/tools/catalog")
def tool_catalog(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    seed_builtin_tools(session)
    session.commit()
    tools = session.exec(
        select(ToolDefinition)
        .where(ToolDefinition.harness_type == "claude_code")
        .order_by(ToolDefinition.tool_key)
    ).all()
    policies = {
        row.tool_definition_id: row
        for row in session.exec(
            select(NamespaceToolPolicy).where(
                NamespaceToolPolicy.namespace_id == namespace_id
            )
        ).all()
    }
    runtimes = session.exec(
        select(RuntimeProfile).where(RuntimeProfile.namespace_id == namespace_id)
    ).all()
    data = []
    for tool in tools:
        policy = policies.get(tool.id)
        availability = []
        for runtime in runtimes:
            reported = runtime.harness_capabilities.get("claude_code", {}).get(
                "builtin_tools"
            )
            availability.append(
                {
                    "runtime_profile_id": runtime.id,
                    "available": None
                    if reported is None
                    else tool.tool_key in reported,
                }
            )
        data.append(
            {
                "id": tool.id,
                "tool_key": tool.tool_key,
                "display_name": tool.display_name,
                "description": tool.description,
                "source": tool.source.value,
                "risk_level": tool.risk_level,
                "baseline_policy": tool.baseline_policy.value,
                "supports_approval": tool.supports_approval,
                "schema_digest": tool.schema_digest,
                "namespace_policy": policy.policy.value if policy else "inherit",
                "target_availability": availability,
            }
        )
    snapshots = session.exec(select(McpToolSnapshot)).all()
    for snapshot in snapshots:
        data.append(
            {
                "id": snapshot.id,
                "tool_key": snapshot.qualified_name,
                "display_name": snapshot.original_name,
                "description": snapshot.description,
                "source": "mcp",
                "risk_level": "external",
                "baseline_policy": "require_approval",
                "supports_approval": True,
                "schema_digest": snapshot.schema_digest,
                "namespace_policy": "inherit",
                "target_availability": [],
            }
        )
    return {"data": data, "count": len(data)}


@router.put("/tools/{tool_key:path}/namespace-policy")
def set_namespace_tool_policy(
    tool_key: str,
    body: ToolIntent,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    seed_builtin_tools(session)
    tool = session.exec(
        select(ToolDefinition).where(
            ToolDefinition.tool_key == tool_key,
            ToolDefinition.harness_type == "claude_code",
        )
    ).first()
    if tool is None:
        raise HTTPException(404, "Tool not found")
    if body.tool_key != tool_key:
        raise HTTPException(422, "tool_key body/path mismatch")
    if body.policy in {ToolPolicy.ALLOW, ToolPolicy.DENY}:
        raise HTTPException(
            422, "Namespace policy must be inherit, disabled, or require_approval"
        )
    if (
        tool.baseline_policy == ToolBaseline.FORBIDDEN
        and body.policy != ToolPolicy.DISABLED
    ):
        raise HTTPException(422, "Platform forbidden Tool cannot be enabled")
    if (
        tool.baseline_policy == ToolBaseline.REQUIRE_APPROVAL
        and body.policy == ToolPolicy.INHERIT
    ):
        pass
    row = session.exec(
        select(NamespaceToolPolicy).where(
            NamespaceToolPolicy.namespace_id == namespace_id,
            NamespaceToolPolicy.tool_definition_id == tool.id,
        )
    ).first()
    if row is None:
        row = NamespaceToolPolicy(
            namespace_id=namespace_id,
            tool_definition_id=tool.id,
            policy=body.policy,
            updated_by=current_user.id,
        )
    else:
        row.policy = body.policy
        row.updated_by = current_user.id
        row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return {"tool_key": tool_key, "policy": row.policy.value}


@router.put("/agents/{agent_id}/draft/skills")
def set_agent_skills(
    agent_id: uuid.UUID,
    body: AgentSkillBindings,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _agent, draft = _get_agent_draft(session, namespace_id, agent_id)
    _advance_draft(draft, body.expected_revision)
    resolved: list[tuple[SkillDefinition, SkillVersion]] = []
    seen: set[uuid.UUID] = set()
    for requested in body.skills:
        version = session.get(SkillVersion, requested.skill_version_id)
        identity = session.get(SkillDefinition, version.skill_id) if version else None
        if version is None or identity is None or identity.namespace_id != namespace_id:
            raise HTTPException(404, "Skill version not found")
        if version.deprecated or identity.archived:
            raise HTTPException(
                422, "Deprecated or archived Skill cannot be newly bound"
            )
        if identity.id in seen:
            raise HTTPException(422, "A Skill may only be bound at one exact version")
        seen.add(identity.id)
        resolved.append((identity, version))
    for row in session.exec(
        select(AgentDraftSkill).where(AgentDraftSkill.agent_draft_id == draft.id)
    ).all():
        session.delete(row)
    for identity, version in resolved:
        session.add(
            AgentDraftSkill(
                agent_draft_id=draft.id,
                skill_id=identity.id,
                skill_version_id=version.id,
            )
        )
    session.add(draft)
    session.commit()
    return {
        "revision": draft.revision,
        "skills": [
            {
                "skill_id": identity.id,
                "skill_version_id": version.id,
                "version": version.version,
            }
            for identity, version in resolved
        ],
    }


@router.put("/agents/{agent_id}/draft/tools")
def set_agent_tools(
    agent_id: uuid.UUID,
    body: AgentToolBindings,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _agent, draft = _get_agent_draft(session, namespace_id, agent_id)
    _advance_draft(draft, body.expected_revision)
    if len({item.tool_key for item in body.tools}) != len(body.tools):
        raise HTTPException(422, "Duplicate Tool policy")
    catalog = {tool.tool_key for tool in session.exec(select(ToolDefinition)).all()}
    mcp_catalog = {
        tool.qualified_name for tool in session.exec(select(McpToolSnapshot)).all()
    }
    for item in body.tools:
        if item.tool_key not in catalog | mcp_catalog:
            raise HTTPException(422, f"Unknown Tool: {item.tool_key}")
    for row in session.exec(
        select(AgentDraftToolPolicy).where(
            AgentDraftToolPolicy.agent_draft_id == draft.id
        )
    ).all():
        session.delete(row)
    for item in body.tools:
        session.add(
            AgentDraftToolPolicy(
                agent_draft_id=draft.id, tool_key=item.tool_key, policy=item.policy
            )
        )
    session.add(draft)
    session.commit()
    return {
        "revision": draft.revision,
        "tools": [item.model_dump(mode="json") for item in body.tools],
    }


@router.get("/mcp-servers")
def list_mcp_servers(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    rows = session.exec(
        select(McpServer)
        .where(McpServer.namespace_id == namespace_id)
        .order_by(col(McpServer.created_at).desc())
    ).all()
    return {"data": [_identity_public(row) for row in rows], "count": len(rows)}


@router.post("/mcp-servers", status_code=201)
def create_mcp_server(
    body: IdentityCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    row = McpServer(
        namespace_id=namespace_id, created_by=current_user.id, **body.model_dump()
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "MCP slug already exists") from exc
    return _identity_public(row)


def _validated_platform_secret(
    transport: McpTransport, secret_inputs: dict[str, str]
) -> tuple[str, str] | None:
    if not secret_inputs:
        return None
    if any(not key or not value for key, value in secret_inputs.items()):
        raise HTTPException(422, "secret_inputs must contain non-empty values")
    if any("\n" in value or "\r" in value for value in secret_inputs.values()):
        raise HTTPException(422, "MCP secret values must not contain line breaks")
    if transport == McpTransport.STDIO:
        denied = [
            key
            for key in secret_inputs
            if key in ENV_DENYLIST
            or any(key.startswith(prefix) for prefix in ENV_DENYLIST_PREFIXES)
        ]
        if denied:
            raise HTTPException(422, {"code": "env_denied", "names": denied})
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in secret_inputs):
            raise HTTPException(
                422, "stdio secret names must be canonical environment names"
            )
    elif any(
        not re.fullmatch(r"[A-Za-z0-9-]+", key)
        or key.lower() in {"host", "content-length", "connection"}
        for key in secret_inputs
    ):
        raise HTTPException(422, "HTTP secret names must be safe request Header names")
    ciphertext = seal_secret_payload(secret_inputs)
    if ciphertext is None:
        raise HTTPException(422, "Secret payload is empty")
    fingerprint = hashlib.sha256(canonical_bytes(sorted(secret_inputs))).hexdigest()
    return ciphertext, fingerprint


@router.post("/mcp-servers/complete", status_code=201)
def create_mcp_server_complete(
    body: McpCompleteCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    diagnostics = validate_mcp_config(
        body.revision.transport,
        body.revision.config,
        allow_http=settings.ENVIRONMENT == "local",
    )
    if diagnostics:
        raise HTTPException(
            422, {"errors": [item.model_dump() for item in diagnostics]}
        )
    runtime = session.get(RuntimeProfile, body.target.runtime_profile_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime target not found")
    if runtime.runtime_type == RuntimeType.NODE and not body.target.secret_ref:
        raise HTTPException(422, "Node MCP target requires a node-local secret_ref")
    if runtime.runtime_type == RuntimeType.PLATFORM and body.target.secret_ref:
        raise HTTPException(422, "Platform target cannot use secret_ref")
    if runtime.runtime_type == RuntimeType.NODE and body.secret_inputs:
        raise HTTPException(422, "Node secrets must be managed locally")
    if body.revision.transport == McpTransport.STDIO:
        executable = body.revision.config.get("executable_key")
        inventory = runtime.harness_capabilities.get("mcp_executables") or []
        if executable not in inventory:
            raise HTTPException(422, "stdio executable is not in target inventory")
    platform_secret = (
        _validated_platform_secret(body.revision.transport, body.secret_inputs)
        if runtime.runtime_type == RuntimeType.PLATFORM
        else None
    )
    server = McpServer(
        namespace_id=namespace_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        created_by=current_user.id,
    )
    digest = canonical_digest(
        {
            "transport": body.revision.transport.value,
            "config": body.revision.config,
            "protocol_version": body.revision.protocol_version,
        }
    )
    revision = McpServerRevision(
        server_id=server.id,
        revision=1,
        transport=body.revision.transport,
        config=body.revision.config,
        config_sha256=digest,
        protocol_version=body.revision.protocol_version,
        created_by=current_user.id,
    )
    target = McpTargetBinding(
        revision_id=revision.id,
        runtime_profile_id=runtime.id,
        secret_ref=body.target.secret_ref,
        capability_fingerprint=runtime_capability_fingerprint(runtime),
    )
    session.add(server)
    session.add(revision)
    session.add(target)
    if platform_secret:
        ciphertext, fingerprint = platform_secret
        target.secret_fingerprint = fingerprint
        target.status = McpTargetStatus.STALE
        session.add(
            McpPlatformSecret(
                target_binding_id=target.id, secret_ciphertext=ciphertext
            )
        )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "MCP identifier or target already exists") from exc
    return {
        "server": _identity_public(server),
        "revision": {
            "id": revision.id,
            "revision": revision.revision,
            "transport": revision.transport.value,
            "config": revision.config,
        },
        "target": {
            "id": target.id,
            "runtime_profile_id": target.runtime_profile_id,
            "status": target.status.value,
        },
    }


def _get_mcp(
    session: SessionDep, server_id: uuid.UUID, namespace_id: uuid.UUID
) -> McpServer:
    row = session.get(McpServer, server_id)
    if row is None or row.namespace_id != namespace_id:
        raise HTTPException(404, "MCP Server not found")
    return row


@router.get("/mcp-servers/{server_id}")
def get_mcp_server(
    server_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    server = _get_mcp(session, server_id, namespace_id)
    revisions = session.exec(
        select(McpServerRevision)
        .where(McpServerRevision.server_id == server.id)
        .order_by(col(McpServerRevision.revision).desc())
    ).all()
    return {
        **_identity_public(server),
        "revisions": [
            {
                "id": row.id,
                "revision": row.revision,
                "transport": row.transport.value,
                "config": row.config,
                "config_sha256": row.config_sha256,
                "protocol_version": row.protocol_version,
                "deprecated": row.deprecated,
                "created_at": row.created_at,
            }
            for row in revisions
        ],
    }


@router.patch("/mcp-servers/{server_id}")
def update_mcp_server(
    server_id: uuid.UUID,
    body: IdentityUpdate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    row = _get_mcp(session, server_id, namespace_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return _identity_public(row)


@router.delete("/mcp-servers/{server_id}", status_code=204)
def delete_mcp_server(
    server_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    server = _get_mcp(session, server_id, namespace_id)
    revision_ids = session.exec(
        select(McpServerRevision.id).where(McpServerRevision.server_id == server.id)
    ).all()
    if (
        revision_ids
        and session.exec(
            select(AgentDraftMcp).where(
                col(AgentDraftMcp.revision_id).in_(revision_ids)
            )
        ).first()
    ):
        raise HTTPException(409, "Referenced MCP Server can only be archived")
    session.delete(server)
    session.commit()


@router.get("/mcp-servers/{server_id}/revisions")
def list_mcp_revisions(
    server_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    server = _get_mcp(session, server_id, namespace_id)
    rows = session.exec(
        select(McpServerRevision)
        .where(McpServerRevision.server_id == server.id)
        .order_by(col(McpServerRevision.revision).desc())
    ).all()
    return {
        "data": [
            {
                "id": row.id,
                "revision": row.revision,
                "transport": row.transport.value,
                "config": row.config,
                "config_sha256": row.config_sha256,
                "protocol_version": row.protocol_version,
                "deprecated": row.deprecated,
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.post("/mcp-servers/{server_id}/revisions", status_code=201)
def create_mcp_revision(
    server_id: uuid.UUID,
    body: McpRevisionCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    server = _get_mcp(session, server_id, namespace_id)
    diagnostics = validate_mcp_config(
        body.transport, body.config, allow_http=settings.ENVIRONMENT == "local"
    )
    if diagnostics:
        raise HTTPException(
            422, {"errors": [item.model_dump() for item in diagnostics]}
        )
    prior = session.exec(
        select(McpServerRevision.revision)
        .where(McpServerRevision.server_id == server.id)
        .order_by(col(McpServerRevision.revision).desc())
    ).first()
    digest = canonical_digest(
        {
            "transport": body.transport.value,
            "config": body.config,
            "protocol_version": body.protocol_version,
        }
    )
    row = McpServerRevision(
        server_id=server.id,
        revision=(prior or 0) + 1,
        transport=body.transport,
        config=body.config,
        config_sha256=digest,
        protocol_version=body.protocol_version,
        created_by=current_user.id,
    )
    session.add(row)
    session.commit()
    return {
        "id": row.id,
        "server_id": row.server_id,
        "revision": row.revision,
        "transport": row.transport.value,
        "config": row.config,
        "config_sha256": row.config_sha256,
        "protocol_version": row.protocol_version,
        "deprecated": row.deprecated,
    }


def _get_mcp_revision(
    session: SessionDep, revision_id: uuid.UUID, namespace_id: uuid.UUID
) -> tuple[McpServer, McpServerRevision]:
    revision = session.get(McpServerRevision, revision_id)
    server = session.get(McpServer, revision.server_id) if revision else None
    if revision is None or server is None or server.namespace_id != namespace_id:
        raise HTTPException(404, "MCP Revision not found")
    return server, revision


@router.get("/mcp-servers/{server_id}/revisions/{revision}")
def get_mcp_revision(
    server_id: uuid.UUID,
    revision: int,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    server = _get_mcp(session, server_id, namespace_id)
    row = session.exec(
        select(McpServerRevision).where(
            McpServerRevision.server_id == server.id,
            McpServerRevision.revision == revision,
        )
    ).first()
    if row is None:
        raise HTTPException(404, "MCP Revision not found")
    targets = session.exec(
        select(McpTargetBinding).where(McpTargetBinding.revision_id == row.id)
    ).all()
    return {
        "id": row.id,
        "server_id": row.server_id,
        "revision": row.revision,
        "transport": row.transport.value,
        "config": row.config,
        "config_sha256": row.config_sha256,
        "protocol_version": row.protocol_version,
        "deprecated": row.deprecated,
        "targets": [
            {
                "id": target.id,
                "runtime_profile_id": target.runtime_profile_id,
                "secret_ref": target.secret_ref,
                "status": target.status.value,
                "tool_digest": target.tool_digest,
                "capability_fingerprint": target.capability_fingerprint,
            }
            for target in targets
        ],
    }


@router.post("/mcp-revisions/{revision_id}/targets", status_code=201)
def create_mcp_target(
    revision_id: uuid.UUID,
    body: McpTargetCreate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _server, revision = _get_mcp_revision(session, revision_id, namespace_id)
    runtime = session.get(RuntimeProfile, body.runtime_profile_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime target not found")
    if runtime.runtime_type == RuntimeType.NODE and not body.secret_ref:
        raise HTTPException(422, "Node MCP target requires a node-local secret_ref")
    if runtime.runtime_type == RuntimeType.PLATFORM and body.secret_ref:
        raise HTTPException(
            422, "Platform MCP target uses the write-only secret endpoint"
        )
    if revision.transport == McpTransport.STDIO:
        executable = revision.config.get("executable_key")
        inventory = runtime.harness_capabilities.get("mcp_executables") or []
        if executable not in inventory:
            raise HTTPException(422, "stdio executable is not in the target inventory")
    row = McpTargetBinding(
        revision_id=revision.id,
        runtime_profile_id=runtime.id,
        secret_ref=body.secret_ref,
        capability_fingerprint=runtime_capability_fingerprint(runtime),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "MCP target binding already exists") from exc
    return {
        "id": row.id,
        "runtime_profile_id": row.runtime_profile_id,
        "secret_ref": row.secret_ref,
        "status": row.status.value,
    }


def _get_mcp_target(
    session: SessionDep, target_id: uuid.UUID, namespace_id: uuid.UUID
) -> tuple[McpServer, McpServerRevision, McpTargetBinding, RuntimeProfile]:
    target = session.get(McpTargetBinding, target_id)
    if target is None:
        raise HTTPException(404, "MCP target not found")
    server, revision = _get_mcp_revision(session, target.revision_id, namespace_id)
    runtime = session.get(RuntimeProfile, target.runtime_profile_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "MCP target not found")
    return server, revision, target, runtime


@router.put("/mcp-targets/{target_id}/secret")
def put_mcp_secret(
    target_id: uuid.UUID,
    body: McpSecretWrite,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _server, revision, target, runtime = _get_mcp_target(
        session, target_id, namespace_id
    )
    if runtime.runtime_type != RuntimeType.PLATFORM:
        raise HTTPException(422, "Node secrets must be managed locally on the node")
    if not body.secret_inputs or any(
        not key or not value for key, value in body.secret_inputs.items()
    ):
        raise HTTPException(422, "secret_inputs must contain non-empty values")
    if any("\n" in value or "\r" in value for value in body.secret_inputs.values()):
        raise HTTPException(422, "MCP secret values must not contain line breaks")
    if revision.transport == McpTransport.STDIO:
        denied = [
            key
            for key in body.secret_inputs
            if key in ENV_DENYLIST
            or any(key.startswith(prefix) for prefix in ENV_DENYLIST_PREFIXES)
        ]
        if denied:
            raise HTTPException(422, {"code": "env_denied", "names": denied})
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in body.secret_inputs):
            raise HTTPException(
                422, "stdio secret names must be canonical environment names"
            )
    elif any(
        not re.fullmatch(r"[A-Za-z0-9-]+", key)
        or key.lower() in {"host", "content-length", "connection"}
        for key in body.secret_inputs
    ):
        raise HTTPException(422, "HTTP secret names must be safe request Header names")
    row = session.exec(
        select(McpPlatformSecret).where(
            McpPlatformSecret.target_binding_id == target.id
        )
    ).first()
    ciphertext = seal_secret_payload(body.secret_inputs)
    if ciphertext is None:
        raise HTTPException(422, "Secret payload is empty")
    if row is None:
        row = McpPlatformSecret(
            target_binding_id=target.id, secret_ciphertext=ciphertext
        )
    else:
        row.secret_ciphertext = ciphertext
        row.updated_at = datetime.now(timezone.utc)
    target.status = McpTargetStatus.STALE
    target.secret_fingerprint = hashlib.sha256(
        canonical_bytes(sorted(body.secret_inputs))
    ).hexdigest()
    session.add(row)
    session.add(target)
    session.commit()
    return {
        "target_binding_id": target.id,
        "secret_masked": "****",
        "updated_at": row.updated_at,
    }


@router.post("/mcp-targets/{target_id}/validate", status_code=202)
def validate_mcp_target(
    target_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _server, _revision, target, runtime = _get_mcp_target(
        session, target_id, namespace_id
    )
    if (
        runtime.runtime_type == RuntimeType.PLATFORM
        and session.exec(
            select(McpPlatformSecret).where(
                McpPlatformSecret.target_binding_id == target.id
            )
        ).first()
        is None
    ):
        raise HTTPException(422, "Platform MCP target secret is not configured")
    target.status = McpTargetStatus.PENDING
    attempt = McpValidationAttempt(
        target_binding_id=target.id,
        status=McpTargetStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    session.add(target)
    session.add(attempt)
    session.commit()
    return {
        "id": attempt.id,
        "target_binding_id": target.id,
        "status": attempt.status.value,
        "expires_at": attempt.expires_at,
    }


def _apply_validation_result(
    attempt: McpValidationAttempt,
    target: McpTargetBinding,
    server: McpServer,
    body: McpValidationResult,
    session: SessionDep,
) -> None:
    if (
        attempt.status != McpTargetStatus.PENDING
        or attempt.expires_at
        and attempt.expires_at <= datetime.now(timezone.utc)
    ):
        attempt.status = McpTargetStatus.EXPIRED
        target.status = McpTargetStatus.EXPIRED
        raise HTTPException(409, "MCP validation attempt is not pending")
    if body.status != "verified":
        attempt.status = McpTargetStatus.FAILED
        attempt.result = {"error": body.error or {"code": "mcp_validation_failed"}}
        target.status = McpTargetStatus.FAILED
    else:
        normalized_tools = []
        for raw in body.tools:
            name = raw.get("name")
            schema = raw.get("input_schema")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9._-]+", name)
                or not isinstance(schema, dict)
                or len(canonical_bytes(schema)) > 256 * 1024
            ):
                raise HTTPException(422, "Invalid or oversized MCP Tool schema")
            normalized_tools.append(
                {
                    "name": name,
                    "description": str(raw.get("description", ""))[:2048],
                    "input_schema": schema,
                    "schema_digest": canonical_digest(schema),
                }
            )
        normalized_tools.sort(key=lambda item: str(item["name"]))
        tool_digest = canonical_digest(normalized_tools)
        previous = target.tool_digest
        attempt.status = McpTargetStatus.VERIFIED
        attempt.result = {
            "tool_digest": tool_digest,
            "tool_count": len(normalized_tools),
        }
        target.status = (
            McpTargetStatus.STALE
            if previous and previous != tool_digest
            else McpTargetStatus.VERIFIED
        )
        target.tool_digest = tool_digest
        target.capability_fingerprint = body.capability_fingerprint
        target.secret_fingerprint = body.secret_fingerprint or target.secret_fingerprint
        for tool in normalized_tools:
            session.add(
                McpToolSnapshot(
                    validation_attempt_id=attempt.id,
                    qualified_name=f"mcp:{server.slug}:{tool['name']}",
                    original_name=tool["name"],
                    description=tool["description"],
                    input_schema=tool["input_schema"],
                    schema_digest=tool["schema_digest"],
                )
            )
    attempt.completed_at = datetime.now(timezone.utc)
    target.updated_at = datetime.now(timezone.utc)
    session.add(attempt)
    session.add(target)
    instance_key = canonical_digest(
        {
            "runtime_profile_id": str(target.runtime_profile_id),
            "revision_id": str(target.revision_id),
            "secret_fingerprint": target.secret_fingerprint,
            "capability_fingerprint": target.capability_fingerprint,
        }
    )
    instance = session.exec(
        select(McpRuntimeInstance).where(
            McpRuntimeInstance.instance_key == instance_key
        )
    ).first()
    if instance is None:
        instance = McpRuntimeInstance(
            target_binding_id=target.id,
            instance_key=instance_key,
            status=McpRuntimeStatus.STARTING,
        )
        session.add(instance)
        session.flush()
    instance.status = (
        McpRuntimeStatus.READY
        if attempt.status == McpTargetStatus.VERIFIED
        else McpRuntimeStatus.FAILED
    )
    instance.tool_digest = target.tool_digest
    instance.last_error = attempt.result.get("error")
    instance.last_heartbeat_at = datetime.now(timezone.utc)
    instance.updated_at = datetime.now(timezone.utc)
    session.add(instance)
    session.add(
        McpRuntimeEvent(
            instance_id=instance.id,
            generation=instance.generation,
            event_type="validation_ready"
            if instance.status == McpRuntimeStatus.READY
            else "validation_failed",
            payload={
                "attempt_id": str(attempt.id),
                "tool_digest": target.tool_digest,
                "error": instance.last_error,
            },
        )
    )


@mcp_internal_router.post("/mcp-validations/claim")
def claim_platform_mcp_validation(
    session: SessionDep, response: Response
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    attempts = session.exec(
        select(McpValidationAttempt)
        .where(McpValidationAttempt.status == McpTargetStatus.PENDING)
        .order_by(col(McpValidationAttempt.created_at))
        .with_for_update(skip_locked=True)
    ).all()
    for attempt in attempts:
        if attempt.expires_at and attempt.expires_at <= now:
            attempt.status = McpTargetStatus.EXPIRED
            session.add(attempt)
            continue
        claimed_until = attempt.result.get("claimed_until")
        if (
            isinstance(claimed_until, str)
            and datetime.fromisoformat(claimed_until) > now
        ):
            continue
        target = session.get(McpTargetBinding, attempt.target_binding_id)
        runtime = (
            session.get(RuntimeProfile, target.runtime_profile_id) if target else None
        )
        revision = (
            session.get(McpServerRevision, target.revision_id) if target else None
        )
        server = session.get(McpServer, revision.server_id) if revision else None
        if (
            target is None
            or runtime is None
            or revision is None
            or server is None
            or runtime.runtime_type != RuntimeType.PLATFORM
        ):
            continue
        secret = session.exec(
            select(McpPlatformSecret).where(
                McpPlatformSecret.target_binding_id == target.id
            )
        ).first()
        if secret is None:
            attempt.status = McpTargetStatus.FAILED
            attempt.result = {"error": {"code": "mcp_secret_missing"}}
            target.status = McpTargetStatus.FAILED
            session.add(attempt)
            session.add(target)
            continue
        capability_inventory = {
            "runtime_type": runtime.runtime_type.value,
            "harness_capabilities": runtime.harness_capabilities,
            "config": {
                "allowed_working_roots": runtime.config.get("allowed_working_roots", [])
            },
        }
        attempt.result = {"claimed_until": (now + timedelta(seconds=60)).isoformat()}
        session.add(attempt)
        session.commit()
        return {
            "attempt_id": attempt.id,
            "target_binding_id": target.id,
            "server_slug": server.slug,
            "transport": revision.transport.value,
            "config": revision.config,
            "protocol_version": revision.protocol_version,
            "secret_inputs": open_secret_payload(secret.secret_ciphertext),
            "capability_inventory": capability_inventory,
        }
    session.commit()
    response.status_code = 204
    return None


@mcp_internal_router.post("/mcp-validations/{attempt_id}/result")
def report_platform_mcp_validation(
    attempt_id: uuid.UUID, body: McpValidationResult, session: SessionDep
) -> dict[str, Any]:
    attempt = session.get(McpValidationAttempt, attempt_id)
    target = (
        session.get(McpTargetBinding, attempt.target_binding_id) if attempt else None
    )
    revision = session.get(McpServerRevision, target.revision_id) if target else None
    server = session.get(McpServer, revision.server_id) if revision else None
    runtime = session.get(RuntimeProfile, target.runtime_profile_id) if target else None
    if (
        attempt is None
        or target is None
        or server is None
        or runtime is None
        or runtime.runtime_type != RuntimeType.PLATFORM
    ):
        raise HTTPException(404, "MCP validation attempt not found")
    if body.capability_fingerprint != runtime_capability_fingerprint(runtime):
        body = McpValidationResult(
            status="failed",
            capability_fingerprint=body.capability_fingerprint,
            error={"code": "stale_capability_fingerprint"},
        )
    _apply_validation_result(attempt, target, server, body, session)
    session.commit()
    return {
        "id": attempt.id,
        "status": attempt.status.value,
        "target_status": target.status.value,
    }


@node_router.post("/mcp-validations/{attempt_id}/result")
def node_mcp_validation_result(
    attempt_id: uuid.UUID, body: McpValidationResult, session: SessionDep
) -> dict[str, Any]:
    # Node authentication is performed by the node transport before forwarding
    # this internal result. This endpoint never accepts or returns secret values.
    attempt = session.get(McpValidationAttempt, attempt_id)
    target = (
        session.get(McpTargetBinding, attempt.target_binding_id) if attempt else None
    )
    revision = session.get(McpServerRevision, target.revision_id) if target else None
    server = session.get(McpServer, revision.server_id) if revision else None
    if attempt is None or target is None or server is None:
        raise HTTPException(404, "MCP validation attempt not found")
    _apply_validation_result(attempt, target, server, body, session)
    session.commit()
    return {
        "id": attempt.id,
        "status": attempt.status.value,
        "target_status": target.status.value,
    }


@router.get("/mcp-targets/{target_id}/validations")
def list_mcp_validations(
    target_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    _get_mcp_target(session, target_id, namespace_id)
    rows = session.exec(
        select(McpValidationAttempt)
        .where(McpValidationAttempt.target_binding_id == target_id)
        .order_by(col(McpValidationAttempt.created_at).desc())
    ).all()
    snapshots = (
        session.exec(
            select(McpToolSnapshot).where(
                col(McpToolSnapshot.validation_attempt_id).in_([row.id for row in rows])
            )
        ).all()
        if rows
        else []
    )
    tools_by_attempt: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        tools_by_attempt.setdefault(snapshot.validation_attempt_id, []).append(
            {
                "qualified_name": snapshot.qualified_name,
                "original_name": snapshot.original_name,
                "description": snapshot.description,
                "input_schema": snapshot.input_schema,
                "schema_digest": snapshot.schema_digest,
            }
        )
    for tools in tools_by_attempt.values():
        tools.sort(key=lambda item: item["qualified_name"])
    return {
        "data": [
            {
                "id": row.id,
                "status": row.status.value,
                "result": row.result,
                "tools": tools_by_attempt.get(row.id, []),
                "expires_at": row.expires_at,
                "created_at": row.created_at,
                "completed_at": row.completed_at,
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.get("/mcp-targets/{target_id}/runtime")
def get_mcp_runtime(
    target_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    _get_mcp_target(session, target_id, namespace_id)
    instance = session.exec(
        select(McpRuntimeInstance)
        .where(McpRuntimeInstance.target_binding_id == target_id)
        .order_by(col(McpRuntimeInstance.updated_at).desc())
    ).first()
    if instance is None:
        return {"instance": None, "events": []}
    events = session.exec(
        select(McpRuntimeEvent)
        .where(McpRuntimeEvent.instance_id == instance.id)
        .order_by(col(McpRuntimeEvent.created_at).desc())
        .limit(100)
    ).all()
    return {
        "instance": {
            "id": instance.id,
            "generation": instance.generation,
            "status": instance.status.value,
            "reference_count": instance.reference_count,
            "restart_count": instance.restart_count,
            "restart_budget": instance.restart_budget,
            "tool_digest": instance.tool_digest,
            "last_error": instance.last_error,
            "last_heartbeat_at": instance.last_heartbeat_at,
            "updated_at": instance.updated_at,
        },
        "events": [
            {
                "generation": event.generation,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }


@router.post("/mcp-targets/{target_id}/runtime/restart", status_code=202)
def restart_mcp_runtime(
    target_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _server, _revision, target, runtime = _get_mcp_target(
        session, target_id, namespace_id
    )
    if (
        runtime.runtime_type == RuntimeType.PLATFORM
        and session.exec(
            select(McpPlatformSecret).where(
                McpPlatformSecret.target_binding_id == target.id
            )
        ).first()
        is None
    ):
        raise HTTPException(422, "Platform MCP target secret is not configured")
    instance = session.exec(
        select(McpRuntimeInstance)
        .where(McpRuntimeInstance.target_binding_id == target.id)
        .order_by(col(McpRuntimeInstance.updated_at).desc())
        .with_for_update()
    ).first()
    if instance is not None:
        instance.generation += 1
        instance.status = McpRuntimeStatus.STARTING
        instance.restart_count = 0
        instance.last_error = None
        instance.updated_at = datetime.now(timezone.utc)
        session.add(instance)
        session.add(
            McpRuntimeEvent(
                instance_id=instance.id,
                generation=instance.generation,
                event_type="restart_requested",
                payload={},
            )
        )
    target.status = McpTargetStatus.PENDING
    attempt = McpValidationAttempt(
        target_binding_id=target.id,
        status=McpTargetStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    session.add(target)
    session.add(attempt)
    session.commit()
    return {
        "id": attempt.id,
        "target_binding_id": target.id,
        "status": attempt.status.value,
        "generation": instance.generation if instance else None,
        "expires_at": attempt.expires_at,
    }


@router.put("/agents/{agent_id}/draft/mcp")
def set_agent_mcp(
    agent_id: uuid.UUID,
    body: AgentMcpBindings,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _agent, draft = _get_agent_draft(session, namespace_id, agent_id)
    _advance_draft(draft, body.expected_revision)
    resolved = []
    seen = set()
    for item in body.mcp:
        server, revision = _get_mcp_revision(session, item.revision_id, namespace_id)
        if server.id in seen:
            raise HTTPException(
                422, "An MCP Server may only be bound at one exact revision"
            )
        if revision.deprecated or server.archived:
            raise HTTPException(422, "Deprecated or archived MCP cannot be newly bound")
        snapshots = {
            tool.qualified_name
            for tool in session.exec(
                select(McpToolSnapshot)
                .join(
                    McpValidationAttempt,
                    col(McpToolSnapshot.validation_attempt_id)
                    == McpValidationAttempt.id,
                )
                .join(
                    McpTargetBinding,
                    col(McpValidationAttempt.target_binding_id) == McpTargetBinding.id,
                )
                .where(McpTargetBinding.revision_id == revision.id)
            ).all()
        }
        if any(name not in snapshots for name in item.allowed_tools):
            raise HTTPException(422, "MCP allowlist contains an undiscovered Tool")
        seen.add(server.id)
        resolved.append((server, revision, item.allowed_tools))
    for row in session.exec(
        select(AgentDraftMcp).where(AgentDraftMcp.agent_draft_id == draft.id)
    ).all():
        session.delete(row)
    for server, revision, allowed_tools in resolved:
        session.add(
            AgentDraftMcp(
                agent_draft_id=draft.id,
                server_id=server.id,
                revision_id=revision.id,
                allowed_tools=sorted(set(allowed_tools)),
            )
        )
    session.add(draft)
    session.commit()
    return {
        "revision": draft.revision,
        "mcp": [
            {"server_id": server.id, "revision_id": revision.id, "allowed_tools": tools}
            for server, revision, tools in resolved
        ],
    }


@router.get("/plugins")
def list_plugins(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    rows = session.exec(
        select(Plugin)
        .where(Plugin.namespace_id == namespace_id)
        .order_by(col(Plugin.created_at).desc())
    ).all()
    return {"data": [_identity_public(row) for row in rows], "count": len(rows)}


@router.post("/plugins", status_code=201)
def create_plugin(
    body: IdentityCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    plugin = Plugin(
        namespace_id=namespace_id, created_by=current_user.id, **body.model_dump()
    )
    draft = PluginDraft(plugin_id=plugin.id)
    session.add(plugin)
    session.add(draft)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Plugin slug already exists") from exc
    return _identity_public(plugin)


@router.post("/plugins/complete", status_code=201)
def create_plugin_complete(
    body: PluginCompleteCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    normalized, errors = _validate_plugin_components(
        session, namespace_id, body.harness_type, body.components
    )
    errors.extend(
        item.model_dump()
        for item in validate_config(body.adapter_config, is_profile=True)
    )
    if errors:
        raise HTTPException(422, {"errors": errors})
    plugin = Plugin(
        namespace_id=namespace_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        created_by=current_user.id,
    )
    draft = PluginDraft(
        plugin_id=plugin.id,
        revision=1,
        harness_type=body.harness_type,
        adapter_schema_version=body.adapter_schema_version,
        adapter_config=body.adapter_config,
        components=normalized,
        validated_revision=1,
        validation_result={
            "status": "validated",
            "revision": 1,
            "errors": [],
            "dependency_graph": normalized,
        },
    )
    session.add(plugin)
    session.add(draft)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Plugin identifier already exists") from exc
    return {"plugin": _identity_public(plugin), "draft": _plugin_draft_public(plugin, draft)}


def _get_plugin(
    session: SessionDep, plugin_id: uuid.UUID, namespace_id: uuid.UUID
) -> tuple[Plugin, PluginDraft]:
    plugin = session.get(Plugin, plugin_id)
    if plugin is None or plugin.namespace_id != namespace_id:
        raise HTTPException(404, "Plugin not found")
    draft = session.exec(
        select(PluginDraft).where(PluginDraft.plugin_id == plugin.id)
    ).first()
    if draft is None:
        raise HTTPException(404, "Plugin draft not found")
    return plugin, draft


def _plugin_draft_public(plugin: Plugin, draft: PluginDraft) -> dict[str, Any]:
    return {
        "plugin": _identity_public(plugin),
        "revision": draft.revision,
        "harness_type": draft.harness_type,
        "adapter_schema_version": draft.adapter_schema_version,
        "adapter_config": draft.adapter_config,
        "components": draft.components,
        "validated_revision": draft.validated_revision,
        "validation_result": draft.validation_result,
        "updated_at": draft.updated_at,
    }


@router.get("/plugins/{plugin_id}")
def get_plugin(
    plugin_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    plugin, draft = _get_plugin(session, plugin_id, namespace_id)
    versions = session.exec(
        select(PluginVersion)
        .where(PluginVersion.plugin_id == plugin.id)
        .order_by(col(PluginVersion.created_at).desc())
    ).all()
    return {
        **_plugin_draft_public(plugin, draft),
        "versions": [_plugin_version_public(row) for row in versions],
    }


@router.patch("/plugins/{plugin_id}")
def update_plugin(
    plugin_id: uuid.UUID,
    body: IdentityUpdate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    plugin, _draft = _get_plugin(session, plugin_id, namespace_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(plugin, field, value)
    plugin.updated_at = datetime.now(timezone.utc)
    session.add(plugin)
    session.commit()
    return _identity_public(plugin)


@router.get("/plugins/{plugin_id}/draft")
def get_plugin_draft(
    plugin_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    return _plugin_draft_public(*_get_plugin(session, plugin_id, namespace_id))


def _validate_plugin_components(
    session: SessionDep,
    namespace_id: uuid.UUID,
    harness_type: str,
    components: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    allowed_types = {"skill", "mcp_server", "tool_policy", "harness_config_fragment"}
    for index, component in enumerate(components):
        kind = component.get("type")
        if kind not in allowed_types:
            errors.append(
                {
                    "code": "unsupported_contribution",
                    "field": f"components[{index}].type",
                    "message": f"Unsupported contribution: {kind}",
                }
            )
            continue
        item = dict(component)
        if kind == "skill":
            version_id = item.get("skill_version_id")
            try:
                version = session.get(SkillVersion, uuid.UUID(str(version_id)))
            except ValueError:
                version = None
            identity = (
                session.get(SkillDefinition, version.skill_id) if version else None
            )
            if (
                version is None
                or identity is None
                or identity.namespace_id != namespace_id
                or version.deprecated
            ):
                errors.append(
                    {
                        "code": "skill_dependency_invalid",
                        "field": f"components[{index}]",
                        "message": "Skill dependency is invalid",
                    }
                )
                continue
            item.update(
                {
                    "skill_id": str(identity.id),
                    "slug": identity.slug,
                    "version": version.version,
                    "digest": version.content_sha256,
                }
            )
            identity_key = (kind, str(identity.id))
        elif kind == "mcp_server":
            revision_id = item.get("revision_id")
            try:
                server, revision = _get_mcp_revision(
                    session, uuid.UUID(str(revision_id)), namespace_id
                )
            except (ValueError, HTTPException):
                errors.append(
                    {
                        "code": "mcp_dependency_invalid",
                        "field": f"components[{index}]",
                        "message": "MCP dependency is invalid",
                    }
                )
                continue
            item.update(
                {
                    "server_id": str(server.id),
                    "slug": server.slug,
                    "revision": revision.revision,
                    "digest": revision.config_sha256,
                    "tool_allowlist": sorted(set(item.get("tool_allowlist", []))),
                }
            )
            identity_key = (kind, str(server.id))
        elif kind == "tool_policy":
            tool_key = item.get("tool_key")
            policy = item.get("policy")
            if not isinstance(tool_key, str) or policy not in {
                "deny",
                "require_approval",
            }:
                errors.append(
                    {
                        "code": "invalid_tool_policy",
                        "field": f"components[{index}]",
                        "message": "Plugin Tool policy may only deny or require approval",
                    }
                )
                continue
            identity_key = (kind, tool_key)
        else:
            fragment = item.get("config", {})
            diagnostics = validate_config(fragment, is_profile=True)
            if diagnostics:
                errors.extend(
                    {
                        **diag.model_dump(),
                        "field": f"components[{index}].config.{diag.field}",
                    }
                    for diag in diagnostics
                )
                continue
            identity_key = (kind, "fragment")
        if identity_key in seen and seen[identity_key] != item:
            errors.append(
                {
                    "code": "component_conflict",
                    "field": f"components[{index}]",
                    "message": f"Conflicting contribution for {identity_key[1]}",
                }
            )
        else:
            seen[identity_key] = item
            normalized.append(item)
    normalized.sort(
        key=lambda item: (
            item["type"],
            str(item.get("slug") or item.get("tool_key") or ""),
            str(item.get("version") or item.get("revision") or ""),
        )
    )
    if harness_type != "claude_code":
        errors.append(
            {
                "code": "unsupported_harness",
                "field": "harness_type",
                "message": "v0.5 only supports claude_code",
            }
        )
    return normalized, errors


@router.put("/plugins/{plugin_id}/draft")
def save_plugin_draft(
    plugin_id: uuid.UUID,
    body: PluginDraftSave,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    plugin, draft = _get_plugin(session, plugin_id, namespace_id)
    if draft.revision != body.expected_revision:
        raise HTTPException(
            409, {"code": "draft_revision_conflict", "current_revision": draft.revision}
        )
    normalized, errors = _validate_plugin_components(
        session, namespace_id, body.harness_type, body.components
    )
    config_errors = validate_config(body.adapter_config, is_profile=True)
    errors.extend(diag.model_dump() for diag in config_errors)
    if errors:
        raise HTTPException(422, {"errors": errors})
    draft.revision += 1
    draft.harness_type = body.harness_type
    draft.adapter_schema_version = body.adapter_schema_version
    draft.adapter_config = body.adapter_config
    draft.components = normalized
    draft.validated_revision = None
    draft.validation_result = None
    draft.updated_at = datetime.now(timezone.utc)
    session.add(draft)
    session.commit()
    return _plugin_draft_public(plugin, draft)


@router.post("/plugins/{plugin_id}/draft/validate")
def validate_plugin_draft(
    plugin_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    plugin, draft = _get_plugin(session, plugin_id, namespace_id)
    normalized, errors = _validate_plugin_components(
        session, namespace_id, draft.harness_type, draft.components
    )
    status = "error" if errors else "validated"
    result = {
        "status": status,
        "revision": draft.revision,
        "errors": errors,
        "dependency_graph": normalized,
    }
    draft.validation_result = result
    draft.validated_revision = draft.revision if not errors else None
    session.add(draft)
    session.commit()
    return result


def _plugin_version_public(row: PluginVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "plugin_id": row.plugin_id,
        "version": row.version,
        "harness_type": row.harness_type,
        "adapter_schema_version": row.adapter_schema_version,
        "manifest": row.manifest,
        "dependency_lock": row.dependency_lock,
        "manifest_digest": row.manifest_digest,
        "signature": row.signature,
        "signing_public_key": row.signing_public_key,
        "deprecated": row.deprecated,
        "created_at": row.created_at,
    }


@router.get("/plugins/{plugin_id}/versions")
def list_plugin_versions(
    plugin_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    plugin, _draft = _get_plugin(session, plugin_id, namespace_id)
    rows = session.exec(
        select(PluginVersion)
        .where(PluginVersion.plugin_id == plugin.id)
        .order_by(col(PluginVersion.created_at).desc())
    ).all()
    return {"data": [_plugin_version_public(row) for row in rows], "count": len(rows)}


@router.post("/plugins/{plugin_id}/versions", status_code=201)
def create_plugin_version(
    plugin_id: uuid.UUID,
    body: PluginVersionCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    if not validate_semver(body.version):
        raise HTTPException(422, "version must be canonical SemVer")
    plugin, draft = _get_plugin(session, plugin_id, namespace_id)
    if (
        draft.revision != body.draft_revision
        or draft.validated_revision != draft.revision
    ):
        raise HTTPException(409, "Plugin draft revision is not currently validated")
    normalized, errors = _validate_plugin_components(
        session, namespace_id, draft.harness_type, draft.components
    )
    if errors:
        raise HTTPException(422, {"errors": errors})
    existing = session.exec(
        select(PluginVersion).where(
            PluginVersion.plugin_id == plugin.id, PluginVersion.version == body.version
        )
    ).first()
    manifest = {
        "schema_version": "1.0",
        "plugin": {"id": str(plugin.id), "slug": plugin.slug, "version": body.version},
        "harness_type": draft.harness_type,
        "adapter_schema_version": draft.adapter_schema_version,
        "adapter_config": draft.adapter_config,
        "provides": normalized,
    }
    digest = canonical_digest(manifest)
    if existing:
        if existing.manifest_digest != digest:
            raise HTTPException(
                409, "Plugin version already exists with a different manifest"
            )
        return _plugin_version_public(existing)
    signer = configured_artifact_signer()
    dependency_lock = {"components": normalized, "digest": canonical_digest(normalized)}
    row = PluginVersion(
        plugin_id=plugin.id,
        version=body.version,
        harness_type=draft.harness_type,
        adapter_schema_version=draft.adapter_schema_version,
        manifest=manifest,
        dependency_lock=dependency_lock,
        manifest_digest=digest,
        signature=signer.sign(canonical_bytes(manifest)),
        signing_public_key=signer.public_key(),
        created_by=current_user.id,
    )
    session.add(row)
    session.commit()
    return _plugin_version_public(row)


@router.get("/plugins/{plugin_id}/versions/{version}")
def get_plugin_version(
    plugin_id: uuid.UUID,
    version: str,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    plugin, _draft = _get_plugin(session, plugin_id, namespace_id)
    row = session.exec(
        select(PluginVersion).where(
            PluginVersion.plugin_id == plugin.id, PluginVersion.version == version
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Plugin version not found")
    return _plugin_version_public(row)


@router.post("/plugins/{plugin_id}/versions/{version}/deprecate")
def deprecate_plugin_version(
    plugin_id: uuid.UUID,
    version: str,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    plugin, _draft = _get_plugin(session, plugin_id, namespace_id)
    row = session.exec(
        select(PluginVersion).where(
            PluginVersion.plugin_id == plugin.id, PluginVersion.version == version
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Plugin version not found")
    row.deprecated = True
    session.add(row)
    session.commit()
    return _plugin_version_public(row)


@router.put("/agents/{agent_id}/draft/plugins")
def set_agent_plugins(
    agent_id: uuid.UUID,
    body: AgentPluginBindings,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _agent, draft = _get_agent_draft(session, namespace_id, agent_id)
    _advance_draft(draft, body.expected_revision)
    resolved = []
    seen = set()
    for item in body.plugins:
        version = session.get(PluginVersion, item.plugin_version_id)
        plugin = session.get(Plugin, version.plugin_id) if version else None
        if version is None or plugin is None or plugin.namespace_id != namespace_id:
            raise HTTPException(404, "Plugin version not found")
        if version.deprecated or plugin.archived:
            raise HTTPException(
                422, "Deprecated or archived Plugin cannot be newly bound"
            )
        if plugin.id in seen:
            raise HTTPException(422, "A Plugin may only be bound at one exact version")
        seen.add(plugin.id)
        resolved.append((plugin, version))
    for row in session.exec(
        select(AgentDraftPlugin).where(AgentDraftPlugin.agent_draft_id == draft.id)
    ).all():
        session.delete(row)
    for plugin, version in resolved:
        session.add(
            AgentDraftPlugin(
                agent_draft_id=draft.id,
                plugin_id=plugin.id,
                plugin_version_id=version.id,
            )
        )
    session.add(draft)
    session.commit()
    return {
        "revision": draft.revision,
        "plugins": [
            {
                "plugin_id": plugin.id,
                "plugin_version_id": version.id,
                "version": version.version,
            }
            for plugin, version in resolved
        ],
    }
