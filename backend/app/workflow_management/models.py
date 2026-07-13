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
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def DateTime(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _DateTime(*args, **kwargs))


def SAEnum(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _SAEnum(*args, **kwargs))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


POSTGRES_JSON = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


class WorkflowScopeType(str, Enum):
    PLATFORM = "platform"
    NAMESPACE = "namespace"


class WorkflowTemplateStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class WorkflowVersionStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    BLOCKED = "blocked"


class WorkflowNodeType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    CODE = "code"


class ConfirmationMode(str, Enum):
    PROCESS = "process_confirmation"
    RESULT = "result_confirmation"
    NONE = "no_confirmation"


class WorkflowInstanceStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowNodeStatus(str, Enum):
    INACTIVE = "inactive"
    READY = "ready"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    UPDATE_REQUIRED = "update_required"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_MANUAL_RESOLUTION = "needs_manual_resolution"


class WorkflowExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    NEEDS_MANUAL_RESOLUTION = "needs_manual_resolution"


class GateType(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"


class ConfirmationDecision(str, Enum):
    SUBMIT = "submit"
    ACCEPT = "accept"
    REJECT = "reject"
    SKIP = "skip"


class ArtifactStorageType(str, Enum):
    GIT = "git"
    OBJECT = "object"
    INLINE = "inline"


class WorkflowTemplate(SQLModel, table=True):
    __tablename__ = "workflow_template"
    __table_args__ = (
        UniqueConstraint(
            "scope_type", "namespace_id", "slug", name="uq_workflow_template_scope_slug"
        ),
        Index(
            "uq_workflow_template_platform_slug",
            "slug",
            unique=True,
            postgresql_where=text("scope_type = 'platform' AND namespace_id IS NULL"),
            sqlite_where=text("scope_type = 'platform' AND namespace_id IS NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scope_type: WorkflowScopeType = Field(
        sa_type=SAEnum(
            WorkflowScopeType,
            name="workflowscopetype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    namespace_id: uuid.UUID | None = Field(
        default=None, foreign_key="namespace.id", ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, sa_column=Column(Text))
    status: WorkflowTemplateStatus = Field(
        default=WorkflowTemplateStatus.ACTIVE,
        sa_type=SAEnum(
            WorkflowTemplateStatus,
            name="workflowtemplatestatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowTemplateVersion(SQLModel, table=True):
    __tablename__ = "workflow_template_version"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_workflow_template_version"),
        UniqueConstraint(
            "template_id", "package_digest", name="uq_workflow_template_digest"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: uuid.UUID = Field(
        foreign_key="workflow_template.id",
        nullable=False,
        ondelete="RESTRICT",
        index=True,
    )
    version: str = Field(max_length=64)
    manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    package_digest: str = Field(max_length=64)
    sdk_version: str = Field(max_length=32)
    status: WorkflowVersionStatus = Field(
        default=WorkflowVersionStatus.ACTIVE,
        sa_type=SAEnum(
            WorkflowVersionStatus,
            name="workflowversionstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    build_metadata: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowNodeDefinition(SQLModel, table=True):
    __tablename__ = "workflow_node_definition"
    __table_args__ = (
        UniqueConstraint(
            "template_version_id", "node_key", name="uq_workflow_node_key"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_version_id: uuid.UUID = Field(
        foreign_key="workflow_template_version.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    node_key: str = Field(max_length=128)
    name: str = Field(max_length=255)
    node_type: WorkflowNodeType = Field(
        sa_type=SAEnum(
            WorkflowNodeType,
            name="workflownodetype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    confirmation_mode: ConfirmationMode = Field(
        sa_type=SAEnum(
            ConfirmationMode,
            name="confirmationmode",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    input_schema: dict[str, Any] = Field(
        sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    output_schema: dict[str, Any] = Field(
        sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    runtime_policy: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    agent_release_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_release.id", ondelete="RESTRICT"
    )
    handler_key: str | None = Field(default=None, max_length=255)
    entry_validator_key: str | None = Field(default=None, max_length=255)
    exit_validator_key: str | None = Field(default=None, max_length=255)
    skippable: bool = False
    skip_output: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON)
    )
    side_effecting: bool = False
    join_policy: str = Field(default="all", max_length=32)
    position: int = 0


class WorkflowEdgeDefinition(SQLModel, table=True):
    __tablename__ = "workflow_edge_definition"
    __table_args__ = (
        UniqueConstraint(
            "template_version_id",
            "source_node_key",
            "target_node_key",
            "condition_key",
            name="uq_workflow_edge",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_version_id: uuid.UUID = Field(
        foreign_key="workflow_template_version.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    source_node_key: str = Field(max_length=128)
    target_node_key: str = Field(max_length=128)
    condition_key: str = Field(default="always", max_length=255)
    condition_config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )


class WorkflowApplication(SQLModel, table=True):
    __tablename__ = "workflow_application"
    __table_args__ = (
        UniqueConstraint("template_id", name="uq_workflow_application_template"),
        UniqueConstraint("route_slug", name="uq_workflow_application_route"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: uuid.UUID = Field(
        foreign_key="workflow_template.id", nullable=False, ondelete="CASCADE"
    )
    component_key: str = Field(max_length=255)
    route_slug: str = Field(max_length=128)
    build_digest: str = Field(max_length=64)
    shell_version: str = Field(max_length=32)


class NamespaceWorkflowEnablement(SQLModel, table=True):
    __tablename__ = "namespace_workflow_enablement"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "template_version_id", name="uq_namespace_workflow_version"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    template_version_id: uuid.UUID = Field(
        foreign_key="workflow_template_version.id", nullable=False, ondelete="RESTRICT"
    )
    enabled: bool = True
    is_default: bool = False
    updated_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowInstance(SQLModel, table=True):
    __tablename__ = "workflow_instance"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_workflow_instance_idempotency"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="RESTRICT", index=True
    )
    template_version_id: uuid.UUID = Field(
        foreign_key="workflow_template_version.id", nullable=False, ondelete="RESTRICT"
    )
    package_digest: str = Field(max_length=64)
    title: str = Field(max_length=255)
    status: WorkflowInstanceStatus = Field(
        default=WorkflowInstanceStatus.PENDING,
        sa_type=SAEnum(
            WorkflowInstanceStatus,
            name="workflowinstancestatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    input: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    default_runtime_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_profile.id", ondelete="RESTRICT"
    )
    runtime_resolution: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    project_context_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    idempotency_key: str = Field(max_length=255)
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
    cancelled_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class WorkflowNodeInstance(SQLModel, table=True):
    __tablename__ = "workflow_node_instance"
    __table_args__ = (
        UniqueConstraint(
            "workflow_instance_id", "node_key", name="uq_workflow_instance_node"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workflow_instance_id: uuid.UUID = Field(
        foreign_key="workflow_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    node_definition_id: uuid.UUID = Field(
        foreign_key="workflow_node_definition.id", nullable=False, ondelete="RESTRICT"
    )
    node_key: str = Field(max_length=128)
    status: WorkflowNodeStatus = Field(
        default=WorkflowNodeStatus.INACTIVE,
        sa_type=SAEnum(
            WorkflowNodeStatus,
            name="workflownodestatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    expected_revision: int = 0
    current_revision_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workflow_node_revision.id",
                name="fk_workflow_node_current_revision",
                ondelete="RESTRICT",
                use_alter=True,
            ),
            nullable=True,
        ),
    )
    assignee_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    latest_input_digest: str | None = Field(default=None, max_length=64)
    resolved_runtime_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id", nullable=False, ondelete="RESTRICT"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowNodeRevision(SQLModel, table=True):
    __tablename__ = "workflow_node_revision"
    __table_args__ = (
        UniqueConstraint(
            "node_instance_id", "revision", name="uq_workflow_node_revision"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_instance_id: uuid.UUID = Field(
        foreign_key="workflow_node_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    revision: int
    input_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    input_digest: str = Field(max_length=64)
    output: dict[str, Any] | None = Field(default=None, sa_column=Column(POSTGRES_JSON))
    output_digest: str | None = Field(default=None, max_length=64)
    change_reason: str = Field(sa_column=Column(Text, nullable=False))
    skipped: bool = False
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowNodeExecution(SQLModel, table=True):
    __tablename__ = "workflow_node_execution"
    __table_args__ = (
        UniqueConstraint(
            "node_instance_id", "attempt", name="uq_workflow_node_attempt"
        ),
        UniqueConstraint(
            "node_instance_id", "idempotency_key", name="uq_workflow_execution_key"
        ),
        Index("ix_workflow_node_execution_status", "node_instance_id", "status"),
        Index(
            "uq_workflow_node_active_execution",
            "node_instance_id",
            "input_revision",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_instance_id: uuid.UUID = Field(
        foreign_key="workflow_node_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    input_revision: int
    attempt: int
    runtime_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id", nullable=False, ondelete="RESTRICT"
    )
    agent_task_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_task.id", ondelete="SET NULL"
    )
    conversation_id: uuid.UUID | None = Field(
        default=None, foreign_key="conversation.id", ondelete="SET NULL"
    )
    status: WorkflowExecutionStatus = Field(
        default=WorkflowExecutionStatus.QUEUED,
        sa_type=SAEnum(
            WorkflowExecutionStatus,
            name="workflowexecutionstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    idempotency_key: str = Field(max_length=255)
    external_state_proof: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON)
    )
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(POSTGRES_JSON))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class WorkflowGateResult(SQLModel, table=True):
    __tablename__ = "workflow_gate_result"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_instance_id: uuid.UUID = Field(
        foreign_key="workflow_node_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    revision: int
    gate_type: GateType = Field(
        sa_type=SAEnum(
            GateType,
            name="workflowgatetype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    validator_key: str = Field(max_length=255)
    validator_version: str = Field(max_length=64)
    input_digest: str = Field(max_length=64)
    passed: bool
    details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowConfirmation(SQLModel, table=True):
    __tablename__ = "workflow_confirmation"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_instance_id: uuid.UUID = Field(
        foreign_key="workflow_node_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    revision: int
    mode: ConfirmationMode = Field(
        sa_type=SAEnum(
            ConfirmationMode,
            name="confirmationmode",
            values_callable=lambda values: [value.value for value in values],
            create_constraint=False,
        )
    )
    decision: ConfirmationDecision = Field(
        sa_type=SAEnum(
            ConfirmationDecision,
            name="confirmationdecision",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    reason: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowArtifact(SQLModel, table=True):
    __tablename__ = "workflow_artifact"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    node_revision_id: uuid.UUID = Field(
        foreign_key="workflow_node_revision.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    name: str = Field(max_length=255)
    storage_type: ArtifactStorageType = Field(
        sa_type=SAEnum(
            ArtifactStorageType,
            name="artifactstoragetype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    storage_ref: str = Field(max_length=2048)
    content_digest: str = Field(max_length=64)
    content_type: str = Field(max_length=255)
    size: int
    artifact_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", POSTGRES_JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class WorkflowEvent(SQLModel, table=True):
    __tablename__ = "workflow_event"
    __table_args__ = (
        UniqueConstraint(
            "workflow_instance_id", "sequence", name="uq_workflow_event_sequence"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workflow_instance_id: uuid.UUID = Field(
        foreign_key="workflow_instance.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    sequence: int
    event_type: str = Field(max_length=128)
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
