import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from sqlalchemy import JSON, Column, Index, Text, UniqueConstraint, text
from sqlalchemy import DateTime as _DateTime
from sqlalchemy import Enum as _SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.runtime.artifacts.manifest import ArtifactKind, LogicalTarget
from app.runtime.policy import TaskKind, TaskStatus


# See app.models: SQLModel's annotation is narrower than its supported runtime API.
def DateTime(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _DateTime(*args, **kwargs))


def SAEnum(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _SAEnum(*args, **kwargs))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


POSTGRES_JSON = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


class RuntimeType(str, Enum):
    PLATFORM = "platform"
    NODE = "node"


class RuntimeRouteMode(str, Enum):
    PLATFORM_GATEWAY = "platform_gateway"
    DIRECT_ANTHROPIC = "direct_anthropic"


class AgentEventType(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATUS = "status"
    ERROR = "error"
    RESULT = "result"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    APPROVAL_EXPIRED = "approval_expired"


class RuntimeJobKind(str, Enum):
    WORKFLOW_HANDLER = "workflow_handler"
    WORKFLOW_VALIDATOR = "workflow_validator"
    REPOSITORY_PROBE = "repository_probe"


class RuntimeJobStatus(str, Enum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_MANUAL_RESOLUTION = "needs_manual_resolution"


class RuntimeProfile(SQLModel, table=True):
    __tablename__ = "runtime_profile"
    __table_args__ = (
        Index(
            "uq_runtime_profile_namespace_platform",
            "namespace_id",
            unique=True,
            postgresql_where=text("runtime_type = 'platform'"),
            sqlite_where=text("runtime_type = 'platform'"),
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE"
    )
    runtime_type: RuntimeType = Field(
        sa_type=SAEnum(
            RuntimeType,
            name="runtimetype",
            values_callable=lambda v: [x.value for x in v],
        )
    )
    route_mode: RuntimeRouteMode = Field(
        sa_type=SAEnum(
            RuntimeRouteMode,
            name="runtimeroutemode",
            values_callable=lambda v: [x.value for x in v],
        )
    )
    provider_config_id: uuid.UUID | None = Field(
        default=None, foreign_key="llm_provider_config.id", ondelete="SET NULL"
    )
    model_id: str = Field(max_length=255)
    base_url: str | None = Field(default=None, max_length=1024)
    permission_mode: str = Field(default="default", max_length=64)
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    harness_capabilities: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class RuntimeSecret(SQLModel, table=True):
    __tablename__ = "runtime_secret"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE"
    )
    runtime_profile_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id",
        nullable=False,
        ondelete="CASCADE",
        unique=True,
    )
    secret_ciphertext: str = Field(sa_column=Column(Text, nullable=False))
    secret_masked: str | None = Field(default=None, max_length=255)


class AgentSession(SQLModel, table=True):
    __tablename__ = "agent_session"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE"
    )
    runtime_profile_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id", nullable=False, ondelete="CASCADE"
    )
    sdk_session_id: str | None = Field(default=None, max_length=255)
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    agent_release_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_release.id", ondelete="SET NULL"
    )
    runtime_agent_release_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_agent_release.id", ondelete="SET NULL"
    )
    resolved_spec_digest: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentTask(SQLModel, table=True):
    __tablename__ = "agent_task"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_agent_task_namespace_idempotency",
        ),
        Index(
            "ix_agent_task_dispatch_candidate",
            "target_node_id",
            "status",
            "dispatch_reserved_until",
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE"
    )
    session_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_session.id", ondelete="SET NULL"
    )
    runtime_profile_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id", nullable=False, ondelete="CASCADE"
    )
    target_node_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_node.id", ondelete="SET NULL", index=True
    )
    task_kind: TaskKind = Field(
        default=TaskKind.ORDINARY,
        sa_type=SAEnum(
            TaskKind,
            name="agenttaskkind",
            values_callable=lambda v: [x.value for x in v],
        ),
    )
    status: TaskStatus = Field(
        default=TaskStatus.QUEUED,
        sa_type=SAEnum(
            TaskStatus,
            name="agenttaskstatus",
            values_callable=lambda v: [x.value for x in v],
        ),
    )
    prompt: str = Field(sa_column=Column(Text, nullable=False))
    snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    final_result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    revision: int = 1
    claimed_by: str | None = Field(default=None, max_length=255)
    lease_expires_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    dispatch_connection_id: uuid.UUID | None = Field(default=None, index=True)
    dispatch_reserved_until: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True), index=True
    )
    retry_of_task_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_task.id", ondelete="SET NULL"
    )
    agent_release_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_release.id", ondelete="SET NULL"
    )
    runtime_agent_release_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_agent_release.id", ondelete="SET NULL"
    )
    resolved_spec_digest: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=255)
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class RuntimeSkillState(SQLModel, table=True):
    __tablename__ = "runtime_skill_state"
    __table_args__ = (
        UniqueConstraint(
            "runtime_profile_id", "skill_id", name="uq_runtime_skill_state"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    runtime_profile_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    skill_id: uuid.UUID = Field(
        foreign_key="skill_definition.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    desired_version_id: uuid.UUID | None = Field(
        default=None, foreign_key="skill_version.id", ondelete="RESTRICT"
    )
    desired_digest: str | None = Field(default=None, max_length=64)
    applied_version_id: uuid.UUID | None = Field(
        default=None, foreign_key="skill_version.id", ondelete="RESTRICT"
    )
    applied_digest: str | None = Field(default=None, max_length=64)
    generation: int = 1
    applied_generation: int | None = None
    status: str = Field(default="pending", max_length=32, index=True)
    subscription_count: int = 0
    retry_count: int = 0
    next_retry_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    last_error: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON, nullable=True)
    )
    last_reconciled_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    applied_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class RuntimeSkillSyncAttempt(SQLModel, table=True):
    __tablename__ = "runtime_skill_sync_attempt"
    __table_args__ = (
        UniqueConstraint(
            "runtime_skill_state_id", "attempt_no", name="uq_runtime_skill_attempt"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    runtime_skill_state_id: uuid.UUID = Field(
        foreign_key="runtime_skill_state.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    attempt_no: int
    generation: int
    version_id: uuid.UUID = Field(
        foreign_key="skill_version.id", nullable=False, ondelete="RESTRICT"
    )
    content_sha256: str = Field(max_length=64)
    trigger: str = Field(default="reconcile", max_length=32)
    status: str = Field(default="pending", max_length=32)
    bytes_downloaded: int = 0
    error: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON, nullable=True)
    )
    started_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AgentTaskSkillUsage(SQLModel, table=True):
    __tablename__ = "agent_task_skill_usage"
    __table_args__ = (
        UniqueConstraint("task_id", "skill_id", name="uq_agent_task_skill_usage"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: uuid.UUID = Field(
        foreign_key="agent_task.id", nullable=False, ondelete="CASCADE", index=True
    )
    skill_id: uuid.UUID = Field(
        foreign_key="skill_definition.id", nullable=False, ondelete="RESTRICT"
    )
    version_id: uuid.UUID = Field(
        foreign_key="skill_version.id", nullable=False, ondelete="RESTRICT"
    )
    version: str = Field(max_length=64)
    content_sha256: str = Field(max_length=64)
    runtime_generation: int
    reported_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class RuntimeJob(SQLModel, table=True):
    __tablename__ = "runtime_job"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_runtime_job_idempotency"
        ),
        Index(
            "ix_runtime_job_dispatch_candidate",
            "target_node_id",
            "status",
            "dispatch_reserved_until",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    runtime_profile_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id", nullable=False, ondelete="CASCADE"
    )
    target_node_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_node.id", ondelete="SET NULL", index=True
    )
    kind: RuntimeJobKind = Field(
        sa_type=SAEnum(
            RuntimeJobKind,
            name="runtimejobkind",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    status: RuntimeJobStatus = Field(
        default=RuntimeJobStatus.QUEUED,
        sa_type=SAEnum(
            RuntimeJobStatus,
            name="runtimejobstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(POSTGRES_JSON))
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(POSTGRES_JSON))
    side_effecting: bool = False
    revision: int = 1
    claimed_by: str | None = Field(default=None, max_length=255)
    lease_expires_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    dispatch_connection_id: uuid.UUID | None = Field(default=None, index=True)
    dispatch_reserved_until: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True), index=True
    )
    idempotency_key: str = Field(max_length=255)
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AgentEvent(SQLModel, table=True):
    __tablename__ = "agent_event"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_agent_event_task_sequence"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE"
    )
    task_id: uuid.UUID = Field(
        foreign_key="agent_task.id", nullable=False, ondelete="CASCADE"
    )
    sequence: int
    event_type: AgentEventType = Field(
        sa_type=SAEnum(
            AgentEventType,
            name="agenteventtype",
            values_callable=lambda v: [x.value for x in v],
        )
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class RuntimeNode(SQLModel, table=True):
    __tablename__ = "runtime_node"
    __table_args__ = (
        UniqueConstraint("key_fingerprint"),
        Index("ix_runtime_node_key_fingerprint", "key_fingerprint"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    runtime_profile_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_profile.id", ondelete="SET NULL", unique=True
    )
    name: str = Field(max_length=255)
    hostname: str = Field(max_length=255)
    os_name: str = Field(max_length=128)
    architecture: str = Field(max_length=64)
    agent_version: str = Field(max_length=64)
    sdk_version: str | None = Field(default=None, max_length=64)
    harness_capabilities: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    public_key: str = Field(sa_column=Column(Text, nullable=False))
    key_fingerprint: str = Field(max_length=128)
    last_seen_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    connected_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    connection_id: uuid.UUID | None = Field(default=None, index=True)
    config_revision: int = 1
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class NodeEnrollmentToken(SQLModel, table=True):
    __tablename__ = "node_enrollment_token"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        Index("ix_node_enrollment_token_token_hash", "token_hash"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    token_hash: str = Field(max_length=64)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    consumed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class NodeCredential(SQLModel, table=True):
    __tablename__ = "node_credential"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_id: uuid.UUID = Field(
        foreign_key="runtime_node.id", nullable=False, ondelete="CASCADE", index=True
    )
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    key_fingerprint: str = Field(max_length=128)
    issued_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    replaced_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="node_credential.id", ondelete="SET NULL"
    )
    replacement_issued_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    replacement_grace_until: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True), index=True
    )


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"


class RuntimeArtifact(SQLModel, table=True):
    __tablename__ = "runtime_artifact"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id",
            "kind",
            "logical_target",
            "version",
            name="uq_runtime_artifact_namespace_version",
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    kind: ArtifactKind = Field(
        sa_type=SAEnum(
            ArtifactKind,
            name="artifactkind",
            values_callable=lambda v: [x.value for x in v],
        )
    )
    logical_target: LogicalTarget = Field(
        sa_type=SAEnum(
            LogicalTarget,
            name="logicaltarget",
            values_callable=lambda v: [x.value for x in v],
        )
    )
    version: str = Field(max_length=128)
    content_sha256: str = Field(max_length=64, index=True)
    storage_key: str = Field(max_length=1024)
    size: int
    manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    signature: str = Field(sa_column=Column(Text, nullable=False))
    signing_public_key: str = Field(sa_column=Column(Text, nullable=False))
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ArtifactRelease(SQLModel, table=True):
    __tablename__ = "artifact_release"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    artifact_id: uuid.UUID = Field(
        foreign_key="runtime_artifact.id", nullable=False, ondelete="RESTRICT"
    )
    valid_until: datetime = Field(sa_type=DateTime(timezone=True))
    rollback_of_release_id: uuid.UUID | None = Field(
        default=None, foreign_key="artifact_release.id", ondelete="SET NULL"
    )
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ArtifactDeployment(SQLModel, table=True):
    __tablename__ = "artifact_deployment"
    __table_args__ = (
        UniqueConstraint(
            "release_id", "node_id", "attempt", name="uq_artifact_deployment_attempt"
        ),
        Index(
            "uq_artifact_deployment_active_target",
            "node_id",
            "logical_target",
            unique=True,
            postgresql_where=text("status IN ('pending', 'dispatched')"),
            sqlite_where=text("status IN ('pending', 'dispatched')"),
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    release_id: uuid.UUID = Field(
        foreign_key="artifact_release.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    node_id: uuid.UUID = Field(
        foreign_key="runtime_node.id", nullable=False, ondelete="CASCADE", index=True
    )
    artifact_id: uuid.UUID = Field(
        foreign_key="runtime_artifact.id", nullable=False, ondelete="RESTRICT"
    )
    previous_artifact_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_artifact.id", ondelete="SET NULL"
    )
    logical_target: LogicalTarget = Field(
        sa_type=SAEnum(
            LogicalTarget,
            name="logicaltarget",
            values_callable=lambda v: [x.value for x in v],
        )
    )
    attempt: int = 1
    status: DeploymentStatus = Field(
        default=DeploymentStatus.PENDING,
        sa_type=SAEnum(
            DeploymentStatus,
            name="deploymentstatus",
            values_callable=lambda v: [x.value for x in v],
        ),
    )
    error: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    dispatched_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    dispatch_connection_id: uuid.UUID | None = Field(default=None, index=True)
    dispatch_reserved_until: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True), index=True
    )
    applied_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class RuntimeNodeArtifact(SQLModel, table=True):
    __tablename__ = "runtime_node_artifact"
    __table_args__ = (
        UniqueConstraint("node_id", "logical_target", name="uq_node_artifact_target"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_id: uuid.UUID = Field(
        foreign_key="runtime_node.id", nullable=False, ondelete="CASCADE", index=True
    )
    logical_target: LogicalTarget = Field(
        sa_type=SAEnum(
            LogicalTarget,
            name="logicaltarget",
            values_callable=lambda v: [x.value for x in v],
        )
    )
    current_artifact_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_artifact.id", ondelete="SET NULL"
    )
    previous_artifact_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_artifact.id", ondelete="SET NULL"
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class NodeHandshakeNonce(SQLModel, table=True):
    __tablename__ = "node_handshake_nonce"
    __table_args__ = (
        UniqueConstraint("nonce_hash"),
        Index("ix_node_handshake_nonce_nonce_hash", "nonce_hash"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_id: uuid.UUID = Field(
        foreign_key="runtime_node.id", nullable=False, ondelete="CASCADE", index=True
    )
    nonce_hash: str = Field(max_length=64)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
