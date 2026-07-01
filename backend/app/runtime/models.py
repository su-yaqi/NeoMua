import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from app.runtime.policy import TaskKind, TaskStatus
from app.runtime.artifacts.manifest import ArtifactKind, LogicalTarget


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    namespace_id: uuid.UUID = Field(foreign_key="namespace.id", nullable=False, ondelete="CASCADE")
    runtime_type: RuntimeType = Field(sa_type=SAEnum(RuntimeType, name="runtimetype", values_callable=lambda v: [x.value for x in v]))
    route_mode: RuntimeRouteMode = Field(sa_type=SAEnum(RuntimeRouteMode, name="runtimeroutemode", values_callable=lambda v: [x.value for x in v]))
    provider_config_id: uuid.UUID | None = Field(default=None, foreign_key="llm_provider_config.id", ondelete="SET NULL")
    model_id: str = Field(max_length=255)
    base_url: str | None = Field(default=None, max_length=1024)
    permission_mode: str = Field(default="default", max_length=64)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class RuntimeSecret(SQLModel, table=True):
    __tablename__ = "runtime_secret"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(foreign_key="namespace.id", nullable=False, ondelete="CASCADE")
    runtime_profile_id: uuid.UUID = Field(foreign_key="runtime_profile.id", nullable=False, ondelete="CASCADE", unique=True)
    secret_ciphertext: str
    secret_masked: str | None = Field(default=None, max_length=255)


class AgentSession(SQLModel, table=True):
    __tablename__ = "agent_session"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(foreign_key="namespace.id", nullable=False, ondelete="CASCADE")
    runtime_profile_id: uuid.UUID = Field(foreign_key="runtime_profile.id", nullable=False, ondelete="CASCADE")
    sdk_session_id: str | None = Field(default=None, max_length=255)
    created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", ondelete="SET NULL")
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class AgentTask(SQLModel, table=True):
    __tablename__ = "agent_task"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_agent_task_namespace_idempotency"
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(foreign_key="namespace.id", nullable=False, ondelete="CASCADE")
    session_id: uuid.UUID | None = Field(default=None, foreign_key="agent_session.id", ondelete="SET NULL")
    runtime_profile_id: uuid.UUID = Field(foreign_key="runtime_profile.id", nullable=False, ondelete="CASCADE")
    target_node_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_node.id", ondelete="SET NULL", index=True
    )
    task_kind: TaskKind = Field(
        default=TaskKind.ORDINARY,
        sa_type=SAEnum(TaskKind, name="agenttaskkind", values_callable=lambda v: [x.value for x in v]),
    )
    status: TaskStatus = Field(default=TaskStatus.QUEUED, sa_type=SAEnum(TaskStatus, name="agenttaskstatus", values_callable=lambda v: [x.value for x in v]))
    prompt: str
    snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    final_result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    revision: int = 1
    claimed_by: str | None = Field(default=None, max_length=255)
    lease_expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    retry_of_task_id: uuid.UUID | None = Field(default=None, foreign_key="agent_task.id", ondelete="SET NULL")
    idempotency_key: str | None = Field(default=None, max_length=255)
    created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", ondelete="SET NULL")
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AgentEvent(SQLModel, table=True):
    __tablename__ = "agent_event"
    __table_args__ = (UniqueConstraint("task_id", "sequence", name="uq_agent_event_task_sequence"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(foreign_key="namespace.id", nullable=False, ondelete="CASCADE")
    task_id: uuid.UUID = Field(foreign_key="agent_task.id", nullable=False, ondelete="CASCADE")
    sequence: int
    event_type: AgentEventType = Field(sa_type=SAEnum(AgentEventType, name="agenteventtype", values_callable=lambda v: [x.value for x in v]))
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class RuntimeNode(SQLModel, table=True):
    __tablename__ = "runtime_node"
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
    public_key: str
    key_fingerprint: str = Field(max_length=128, unique=True, index=True)
    last_seen_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    connected_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    connection_id: uuid.UUID | None = Field(default=None, index=True)
    config_revision: int = 1
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class NodeEnrollmentToken(SQLModel, table=True):
    __tablename__ = "node_enrollment_token"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    token_hash: str = Field(max_length=64, unique=True, index=True)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    consumed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


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
            "namespace_id", "kind", "logical_target", "version",
            name="uq_runtime_artifact_namespace_version",
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    kind: ArtifactKind = Field(
        sa_type=SAEnum(ArtifactKind, name="artifactkind", values_callable=lambda v: [x.value for x in v])
    )
    logical_target: LogicalTarget = Field(
        sa_type=SAEnum(LogicalTarget, name="logicaltarget", values_callable=lambda v: [x.value for x in v])
    )
    version: str = Field(max_length=128)
    content_sha256: str = Field(max_length=64, index=True)
    storage_key: str = Field(max_length=1024)
    size: int
    manifest: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    signature: str
    signing_public_key: str
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


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
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class ArtifactDeployment(SQLModel, table=True):
    __tablename__ = "artifact_deployment"
    __table_args__ = (
        UniqueConstraint(
            "release_id", "node_id", "attempt", name="uq_artifact_deployment_attempt"
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    release_id: uuid.UUID = Field(
        foreign_key="artifact_release.id", nullable=False, ondelete="CASCADE", index=True
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
    attempt: int = 1
    status: DeploymentStatus = Field(
        default=DeploymentStatus.PENDING,
        sa_type=SAEnum(DeploymentStatus, name="deploymentstatus", values_callable=lambda v: [x.value for x in v]),
    )
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    dispatched_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    applied_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


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
        sa_type=SAEnum(LogicalTarget, name="logicaltarget", values_callable=lambda v: [x.value for x in v])
    )
    current_artifact_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_artifact.id", ondelete="SET NULL"
    )
    previous_artifact_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_artifact.id", ondelete="SET NULL"
    )
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
