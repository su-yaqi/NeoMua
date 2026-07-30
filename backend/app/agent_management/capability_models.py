"""Persistent v0.5 capability, release, activation, and approval models.

The tables deliberately keep immutable revisions/versions separate from their
mutable identities and drafts.  Runtime snapshots reference immutable rows;
they never read mutable drafts while executing.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import DateTime as _DateTime
from sqlalchemy import Enum as _SAEnum
from sqlmodel import Field, SQLModel


def DateTime(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _DateTime(*args, **kwargs))


def SAEnum(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _SAEnum(*args, **kwargs))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolSource(str, Enum):
    BUILTIN = "builtin"
    MCP = "mcp"


class ToolBaseline(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    FORBIDDEN = "forbidden"


class ToolPolicy(str, Enum):
    INHERIT = "inherit"
    ALLOW = "allow"
    DENY = "deny"
    DISABLED = "disabled"
    REQUIRE_APPROVAL = "require_approval"


class SkillDefinition(SQLModel, table=True):
    __tablename__ = "skill_definition"
    __table_args__ = (
        UniqueConstraint("namespace_id", "slug", name="uq_skill_namespace_slug"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = None
    archived: bool = False
    current_version_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "skill_version.id",
                name="fk_skill_definition_current_version_id",
                ondelete="RESTRICT",
                use_alter=True,
            ),
            nullable=True,
            index=True,
        ),
    )
    draft_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "skill_draft.id",
                name="fk_skill_definition_draft_id",
                ondelete="SET NULL",
                use_alter=True,
            ),
            nullable=True,
            index=True,
        ),
    )
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class SkillVersion(SQLModel, table=True):
    __tablename__ = "skill_version"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    skill_id: uuid.UUID = Field(
        foreign_key="skill_definition.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    version: str = Field(max_length=64)
    content_sha256: str = Field(max_length=64, index=True)
    storage_key: str = Field(max_length=1024)
    size: int
    manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    invocation_mode: str = Field(max_length=64)
    platforms: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    required_capabilities: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    content_types: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    validation_result: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    manifest_digest: str | None = Field(default=None, max_length=64)
    signature: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    signing_public_key: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    deprecated: bool = False
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    published_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class SkillDraft(SQLModel, table=True):
    __tablename__ = "skill_draft"
    __table_args__ = (UniqueConstraint("skill_id", name="uq_skill_draft_skill_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    skill_id: uuid.UUID = Field(
        foreign_key="skill_definition.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    revision: int = 1
    base_version_id: uuid.UUID | None = Field(
        default=None, foreign_key="skill_version.id", ondelete="SET NULL"
    )
    content_sha256: str | None = Field(default=None, max_length=64)
    validated_revision: int | None = None
    validation_digest: str | None = Field(default=None, max_length=64)
    validation_result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    updated_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class SkillDraftFile(SQLModel, table=True):
    __tablename__ = "skill_draft_file"
    __table_args__ = (
        UniqueConstraint("draft_id", "path", name="uq_skill_draft_file_path"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    draft_id: uuid.UUID = Field(
        foreign_key="skill_draft.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    path: str = Field(max_length=1024)
    mime_type: str = Field(max_length=255)
    size: int
    content_sha256: str = Field(max_length=64, index=True)
    storage_key: str = Field(max_length=1024)
    is_text: bool = True
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class SkillCurrentVersionChange(SQLModel, table=True):
    __tablename__ = "skill_current_version_change"
    __table_args__ = (
        UniqueConstraint(
            "skill_id", "idempotency_key", name="uq_skill_current_version_change_key"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    skill_id: uuid.UUID = Field(
        foreign_key="skill_definition.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    from_version_id: uuid.UUID | None = Field(
        default=None, foreign_key="skill_version.id", ondelete="RESTRICT"
    )
    to_version_id: uuid.UUID = Field(
        foreign_key="skill_version.id", nullable=False, ondelete="RESTRICT"
    )
    action: str = Field(max_length=32)
    idempotency_key: str = Field(max_length=255)
    changed_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ToolDefinition(SQLModel, table=True):
    __tablename__ = "tool_definition"
    __table_args__ = (
        UniqueConstraint(
            "harness_type",
            "tool_key",
            "adapter_schema_version",
            name="uq_tool_definition",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    harness_type: str = Field(max_length=64)
    tool_key: str = Field(max_length=512, index=True)
    display_name: str = Field(max_length=255)
    description: str | None = None
    source: ToolSource = Field(
        sa_type=SAEnum(
            ToolSource,
            name="toolsource",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    risk_level: str = Field(max_length=32)
    baseline_policy: ToolBaseline = Field(
        sa_type=SAEnum(
            ToolBaseline,
            name="toolbaseline",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    supports_approval: bool = True
    adapter_schema_version: str = Field(max_length=32, default="1.0")
    schema_digest: str = Field(max_length=64)


class NamespaceToolPolicy(SQLModel, table=True):
    __tablename__ = "namespace_tool_policy"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "tool_definition_id", name="uq_namespace_tool_policy"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    tool_definition_id: uuid.UUID = Field(
        foreign_key="tool_definition.id", nullable=False, ondelete="CASCADE"
    )
    policy: ToolPolicy = Field(
        sa_type=SAEnum(
            ToolPolicy,
            name="toolpolicy",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    updated_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentDraftSkill(SQLModel, table=True):
    __tablename__ = "agent_draft_skill"
    __table_args__ = (
        UniqueConstraint("agent_draft_id", "skill_id", name="uq_agent_draft_skill"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    agent_draft_id: uuid.UUID = Field(
        foreign_key="agent_draft.id", nullable=False, ondelete="CASCADE", index=True
    )
    skill_id: uuid.UUID = Field(
        foreign_key="skill_definition.id", nullable=False, ondelete="RESTRICT"
    )
    skill_version_id: uuid.UUID | None = Field(
        default=None, foreign_key="skill_version.id", nullable=True, ondelete="RESTRICT"
    )
    enabled: bool = True


class AgentDraftToolPolicy(SQLModel, table=True):
    __tablename__ = "agent_draft_tool_policy"
    __table_args__ = (
        UniqueConstraint("agent_draft_id", "tool_key", name="uq_agent_draft_tool"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    agent_draft_id: uuid.UUID = Field(
        foreign_key="agent_draft.id", nullable=False, ondelete="CASCADE", index=True
    )
    tool_key: str = Field(max_length=512)
    policy: ToolPolicy = Field(
        sa_type=SAEnum(
            ToolPolicy,
            name="agenttoolpolicy",
            values_callable=lambda values: [value.value for value in values],
        )
    )


class McpTransport(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class McpTargetStatus(str, Enum):
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    STALE = "stale"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    EXPIRED = "expired"


class McpRuntimeStatus(str, Enum):
    STARTING = "starting"
    READY = "ready"
    RECONNECTING = "reconnecting"
    STALE = "stale"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class McpServer(SQLModel, table=True):
    __tablename__ = "mcp_server"
    __table_args__ = (
        UniqueConstraint("namespace_id", "slug", name="uq_mcp_namespace_slug"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = None
    archived: bool = False
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class McpServerRevision(SQLModel, table=True):
    __tablename__ = "mcp_server_revision"
    __table_args__ = (
        UniqueConstraint("server_id", "revision", name="uq_mcp_revision"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    server_id: uuid.UUID = Field(
        foreign_key="mcp_server.id", nullable=False, ondelete="CASCADE", index=True
    )
    revision: int
    transport: McpTransport = Field(
        sa_type=SAEnum(
            McpTransport,
            name="mcptransport",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    config_sha256: str = Field(max_length=64)
    protocol_version: str = Field(max_length=64, default="2025-06-18")
    deprecated: bool = False
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class McpTargetBinding(SQLModel, table=True):
    __tablename__ = "mcp_target_binding"
    __table_args__ = (
        UniqueConstraint("revision_id", "runtime_profile_id", name="uq_mcp_target"),
        Index(
            "uq_mcp_target_v09",
            "revision_id",
            "runtime_instance_id",
            unique=True,
            postgresql_where=text("runtime_instance_id IS NOT NULL"),
            sqlite_where=text("runtime_instance_id IS NOT NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    revision_id: uuid.UUID = Field(
        foreign_key="mcp_server_revision.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    runtime_profile_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_profile.id", ondelete="CASCADE", index=True
    )
    runtime_instance_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="runtime_instance.id",
        ondelete="RESTRICT",
        index=True,
    )
    secret_ref: str | None = Field(default=None, max_length=255)
    capability_fingerprint: str | None = Field(default=None, max_length=255)
    secret_fingerprint: str | None = Field(default=None, max_length=255)
    tool_digest: str | None = Field(default=None, max_length=64)
    status: McpTargetStatus = Field(
        default=McpTargetStatus.UNVERIFIED,
        sa_type=SAEnum(
            McpTargetStatus,
            name="mcptargetstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class McpPlatformSecret(SQLModel, table=True):
    __tablename__ = "mcp_platform_secret"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    target_binding_id: uuid.UUID = Field(
        foreign_key="mcp_target_binding.id",
        nullable=False,
        ondelete="CASCADE",
        unique=True,
    )
    secret_ciphertext: str
    secret_masked: str = Field(default="****", max_length=255)
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class McpValidationAttempt(SQLModel, table=True):
    __tablename__ = "mcp_validation_attempt"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    target_binding_id: uuid.UUID = Field(
        foreign_key="mcp_target_binding.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    status: McpTargetStatus = Field(
        sa_type=SAEnum(
            McpTargetStatus,
            name="mcpvalidationstatus",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    result: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class McpToolSnapshot(SQLModel, table=True):
    __tablename__ = "mcp_tool_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "validation_attempt_id", "qualified_name", name="uq_mcp_snapshot_tool"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    validation_attempt_id: uuid.UUID = Field(
        foreign_key="mcp_validation_attempt.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    qualified_name: str = Field(max_length=512)
    original_name: str = Field(max_length=255)
    description: str | None = None
    input_schema: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    schema_digest: str = Field(max_length=64)


class McpRuntimeInstance(SQLModel, table=True):
    __tablename__ = "mcp_runtime_instance"
    __table_args__ = (
        UniqueConstraint("instance_key", name="uq_mcp_runtime_instance_key"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    target_binding_id: uuid.UUID = Field(
        foreign_key="mcp_target_binding.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    instance_key: str = Field(max_length=64, index=True)
    generation: int = 1
    status: McpRuntimeStatus = Field(
        sa_type=SAEnum(
            McpRuntimeStatus,
            name="mcpruntimestatus",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    reference_count: int = 0
    restart_count: int = 0
    restart_budget: int = 5
    tool_digest: str | None = Field(default=None, max_length=64)
    last_error: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    last_heartbeat_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class McpRuntimeEvent(SQLModel, table=True):
    __tablename__ = "mcp_runtime_event"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    instance_id: uuid.UUID = Field(
        foreign_key="mcp_runtime_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    generation: int
    event_type: str = Field(max_length=64)
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentDraftMcp(SQLModel, table=True):
    __tablename__ = "agent_draft_mcp"
    __table_args__ = (
        UniqueConstraint("agent_draft_id", "server_id", name="uq_agent_draft_mcp"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    agent_draft_id: uuid.UUID = Field(
        foreign_key="agent_draft.id", nullable=False, ondelete="CASCADE", index=True
    )
    server_id: uuid.UUID = Field(
        foreign_key="mcp_server.id", nullable=False, ondelete="RESTRICT"
    )
    revision_id: uuid.UUID = Field(
        foreign_key="mcp_server_revision.id", nullable=False, ondelete="RESTRICT"
    )
    allowed_tools: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )


class Plugin(SQLModel, table=True):
    __tablename__ = "neomua_plugin"
    __table_args__ = (
        UniqueConstraint("namespace_id", "slug", name="uq_plugin_namespace_slug"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = None
    archived: bool = False
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class PluginDraft(SQLModel, table=True):
    __tablename__ = "plugin_draft"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plugin_id: uuid.UUID = Field(
        foreign_key="neomua_plugin.id", nullable=False, ondelete="CASCADE", unique=True
    )
    revision: int = 1
    harness_type: str = Field(default="claude_code", max_length=64)
    adapter_schema_version: str = Field(default="1.0", max_length=32)
    adapter_config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    components: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    validation_result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    validated_revision: int | None = None
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class PluginVersion(SQLModel, table=True):
    __tablename__ = "plugin_version"
    __table_args__ = (
        UniqueConstraint("plugin_id", "version", name="uq_plugin_version"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plugin_id: uuid.UUID = Field(
        foreign_key="neomua_plugin.id", nullable=False, ondelete="CASCADE", index=True
    )
    version: str = Field(max_length=64)
    harness_type: str = Field(max_length=64)
    adapter_schema_version: str = Field(max_length=32)
    manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    dependency_lock: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    manifest_digest: str = Field(max_length=64)
    signature: str
    signing_public_key: str
    deprecated: bool = False
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentDraftPlugin(SQLModel, table=True):
    __tablename__ = "agent_draft_plugin"
    __table_args__ = (
        UniqueConstraint("agent_draft_id", "plugin_id", name="uq_agent_draft_plugin"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    agent_draft_id: uuid.UUID = Field(
        foreign_key="agent_draft.id", nullable=False, ondelete="CASCADE", index=True
    )
    plugin_id: uuid.UUID = Field(
        foreign_key="neomua_plugin.id", nullable=False, ondelete="RESTRICT"
    )
    plugin_version_id: uuid.UUID = Field(
        foreign_key="plugin_version.id", nullable=False, ondelete="RESTRICT"
    )


class AgentRelease(SQLModel, table=True):
    __tablename__ = "agent_release"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_release_version"),
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_agent_release_idempotency"
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    agent_id: uuid.UUID = Field(
        foreign_key="agent_definition.id",
        nullable=False,
        ondelete="RESTRICT",
        index=True,
    )
    version: str = Field(max_length=64)
    draft_revision: int
    preferred_model_definition_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="llm_model_definition.id",
        ondelete="RESTRICT",
        index=True,
    )
    required_capabilities: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    resolved_spec_schema_version: str = Field(max_length=32)
    resolved_spec: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    resolved_spec_digest: str = Field(max_length=64, index=True)
    manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    dependency_lock: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    manifest_digest: str = Field(max_length=64)
    signature: str
    signing_public_key: str
    idempotency_key: str = Field(max_length=255)
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentReleaseComponent(SQLModel, table=True):
    __tablename__ = "agent_release_component"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    release_id: uuid.UUID = Field(
        foreign_key="agent_release.id", nullable=False, ondelete="CASCADE", index=True
    )
    component_type: str = Field(max_length=64)
    component_key: str = Field(max_length=512)
    component_version: str | None = Field(default=None, max_length=128)
    source_chain: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )


class ActivationStatus(str, Enum):
    VALIDATING = "validating"
    DEPLOYING = "deploying"
    ACTIVE = "active"
    PARTIAL = "partial"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    EXPIRED = "expired"


class AgentDeploymentStatus(str, Enum):
    PRECHECKING = "prechecking"
    INCOMPATIBLE = "incompatible"
    PENDING = "pending"
    DISPATCHED = "dispatched"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"
    ROLLED_BACK = "rolled_back"


class AgentActivation(SQLModel, table=True):
    __tablename__ = "agent_activation"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_agent_activation_idempotency"
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    release_id: uuid.UUID = Field(
        foreign_key="agent_release.id", nullable=False, ondelete="RESTRICT"
    )
    runtime_instance_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_instance.id", ondelete="RESTRICT", index=True
    )
    precheck_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_activation_precheck.id", ondelete="RESTRICT"
    )
    idempotency_key: str = Field(max_length=255)
    status: ActivationStatus = Field(
        default=ActivationStatus.VALIDATING,
        sa_type=SAEnum(
            ActivationStatus,
            name="activationstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    rollback_of_activation_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_activation.id", ondelete="SET NULL"
    )
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentDeployment(SQLModel, table=True):
    __tablename__ = "agent_deployment"
    __table_args__ = (
        UniqueConstraint(
            "activation_id",
            "runtime_profile_id",
            "attempt",
            name="uq_agent_deployment_attempt",
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    activation_id: uuid.UUID = Field(
        foreign_key="agent_activation.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    runtime_profile_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_profile.id", ondelete="CASCADE", index=True
    )
    runtime_instance_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_instance.id", ondelete="RESTRICT", index=True
    )
    attempt: int = 1
    status: AgentDeploymentStatus = Field(
        default=AgentDeploymentStatus.PRECHECKING,
        sa_type=SAEnum(
            AgentDeploymentStatus,
            name="agentdeploymentstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    capability_fingerprint: str | None = Field(default=None, max_length=255)
    applied_digest: str | None = Field(default=None, max_length=64)
    error: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class RuntimeAgentRelease(SQLModel, table=True):
    __tablename__ = "runtime_agent_release"
    __table_args__ = (
        UniqueConstraint(
            "runtime_profile_id", "agent_id", name="uq_runtime_agent_release"
        ),
        Index(
            "uq_runtime_agent_release_v09",
            "runtime_instance_id",
            "agent_id",
            unique=True,
            postgresql_where=text("runtime_instance_id IS NOT NULL"),
            sqlite_where=text("runtime_instance_id IS NOT NULL"),
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    runtime_profile_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_profile.id", ondelete="CASCADE", index=True
    )
    runtime_instance_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_instance.id", ondelete="RESTRICT", index=True
    )
    agent_id: uuid.UUID = Field(
        foreign_key="agent_definition.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    current_release_id: uuid.UUID = Field(
        foreign_key="agent_release.id", nullable=False, ondelete="RESTRICT"
    )
    previous_release_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_release.id", ondelete="RESTRICT"
    )
    applied_digest: str = Field(max_length=64)
    materialization_digest: str = Field(max_length=64)
    runtime_configuration_revision_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="runtime_configuration_revision.id",
        ondelete="RESTRICT",
    )
    runtime_capability_report_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_capability_report.id", ondelete="RESTRICT"
    )
    adapter_version: str | None = Field(default=None, max_length=64)
    runtime_model_catalog_fingerprint: str | None = Field(default=None, max_length=64)
    effective_spec_digest: str | None = Field(default=None, max_length=64)
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentReleaseRuntimeCompatibility(SQLModel, table=True):
    __tablename__ = "agent_release_runtime_compatibility"
    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "runtime_instance_id",
            name="uq_agent_release_runtime_compatibility",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    release_id: uuid.UUID = Field(
        foreign_key="agent_release.id", nullable=False, ondelete="CASCADE", index=True
    )
    runtime_instance_id: uuid.UUID = Field(
        foreign_key="runtime_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    runtime_capability_report_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_capability_report.id", ondelete="SET NULL"
    )
    model_catalog_fingerprint: str | None = Field(default=None, max_length=64)
    deployable: bool = False
    preference_status: str = Field(default="unavailable", max_length=32)
    diagnostics: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    checked_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentActivationPrecheck(SQLModel, table=True):
    __tablename__ = "agent_activation_precheck"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    release_id: uuid.UUID = Field(
        foreign_key="agent_release.id", nullable=False, ondelete="CASCADE", index=True
    )
    runtime_instance_id: uuid.UUID = Field(
        foreign_key="runtime_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    runtime_configuration_revision_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="runtime_configuration_revision.id",
        ondelete="SET NULL",
    )
    runtime_capability_report_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_capability_report.id", ondelete="SET NULL"
    )
    model_catalog_fingerprint: str = Field(max_length=64)
    preference_status: str = Field(max_length=32)
    deployable: bool
    checks: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    precheck_digest: str = Field(max_length=64, index=True)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ToolApprovalRequest(SQLModel, table=True):
    __tablename__ = "tool_approval_request"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "task_revision",
            "tool_call_id",
            "args_digest",
            name="uq_tool_approval_call",
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    task_id: uuid.UUID = Field(
        foreign_key="agent_task.id", nullable=False, ondelete="CASCADE", index=True
    )
    task_revision: int
    tool_call_id: str = Field(max_length=255)
    tool_qualified_name: str = Field(max_length=512)
    redacted_args: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    args_digest: str = Field(max_length=64)
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        sa_type=SAEnum(
            ApprovalStatus,
            name="approvalstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    requested_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    resolved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    resolved_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    decision_reason: str | None = Field(default=None, max_length=1024)


class CliSession(SQLModel, table=True):
    __tablename__ = "cli_session"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    family_id: uuid.UUID = Field(default_factory=uuid.uuid4, index=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    token_hash: str = Field(max_length=64, unique=True, index=True)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
    absolute_expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    replaced_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="cli_session.id", ondelete="SET NULL"
    )
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    last_used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    client_name: str = Field(max_length=255)
