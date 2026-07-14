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


class ConversationMode(str, Enum):
    CHAT = "chat"
    AGENT = "agent"


class ConversationVisibility(str, Enum):
    PRIVATE = "private"
    PROJECT = "project"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ConversationAgentRole(str, Enum):
    MAIN = "main"
    COLLABORATOR = "collaborator"


class MessageAuthorType(str, Enum):
    USER = "user"
    MODEL = "model"
    AGENT = "agent"
    SYSTEM = "system"


class MessageTargetType(str, Enum):
    MAIN = "main"
    AGENT = "agent"
    ALL = "all"
    MODEL = "model"
    SYSTEM = "system"


class MessageStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DelegationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AttachmentScanStatus(str, Enum):
    PENDING = "pending"
    CLEAN = "clean"
    REJECTED = "rejected"


class Conversation(SQLModel, table=True):
    __tablename__ = "conversation"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id",
            "creator_id",
            "idempotency_key",
            name="uq_conversation_create_idempotency",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    creator_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="RESTRICT", index=True
    )
    project_id: uuid.UUID | None = Field(
        default=None, foreign_key="project.id", ondelete="RESTRICT", index=True
    )
    source_conversation_id: uuid.UUID | None = Field(
        default=None, foreign_key="conversation.id", ondelete="SET NULL"
    )
    title: str = Field(max_length=255)
    mode: ConversationMode = Field(
        sa_type=SAEnum(
            ConversationMode,
            name="conversationmode",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    visibility: ConversationVisibility = Field(
        default=ConversationVisibility.PRIVATE,
        sa_type=SAEnum(
            ConversationVisibility,
            name="conversationvisibility",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    status: ConversationStatus = Field(
        default=ConversationStatus.ACTIVE,
        sa_type=SAEnum(
            ConversationStatus,
            name="conversationstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    runtime_id: uuid.UUID = Field(
        foreign_key="runtime_profile.id", nullable=False, ondelete="RESTRICT"
    )
    provider_config_id: uuid.UUID | None = Field(
        default=None, foreign_key="llm_provider_config.id", ondelete="RESTRICT"
    )
    model_id: str | None = Field(default=None, max_length=255)
    idempotency_key: str = Field(max_length=255)
    creation_fingerprint: str = Field(max_length=64)
    current_context_snapshot_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "conversation_context_snapshot.id",
                name="fk_conversation_current_context_snapshot",
                ondelete="RESTRICT",
                use_alter=True,
            ),
            nullable=True,
        ),
    )
    current_configuration_revision_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "conversation_configuration_revision.id",
                name="fk_conversation_current_configuration_revision",
                ondelete="RESTRICT",
                use_alter=True,
            ),
            nullable=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    archived_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ConversationAgent(SQLModel, table=True):
    __tablename__ = "conversation_agent"
    __table_args__ = (
        UniqueConstraint("conversation_id", "agent_id", name="uq_conversation_agent"),
        Index(
            "uq_conversation_main_agent",
            "conversation_id",
            unique=True,
            postgresql_where=text("role = 'main'"),
            sqlite_where=text("role = 'main'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE", index=True
    )
    role: ConversationAgentRole = Field(
        sa_type=SAEnum(
            ConversationAgentRole,
            name="conversationagentrole",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    agent_id: uuid.UUID = Field(
        foreign_key="agent_definition.id", nullable=False, ondelete="RESTRICT"
    )
    agent_release_id: uuid.UUID = Field(
        foreign_key="agent_release.id", nullable=False, ondelete="RESTRICT"
    )
    runtime_agent_release_id: uuid.UUID = Field(
        foreign_key="runtime_agent_release.id", nullable=False, ondelete="RESTRICT"
    )
    agent_session_id: uuid.UUID = Field(
        foreign_key="agent_session.id", nullable=False, ondelete="RESTRICT"
    )
    resolved_spec_digest: str = Field(max_length=64)
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ConversationConfigurationRevision(SQLModel, table=True):
    __tablename__ = "conversation_configuration_revision"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "revision",
            name="uq_conversation_configuration_revision",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE", index=True
    )
    revision: int
    mode: ConversationMode = Field(
        sa_type=SAEnum(
            ConversationMode,
            name="conversationmode",
            values_callable=lambda values: [value.value for value in values],
            create_type=False,
        )
    )
    provider_config_id: uuid.UUID | None = Field(
        default=None, foreign_key="llm_provider_config.id", ondelete="RESTRICT"
    )
    model_id: str | None = Field(default=None, max_length=255)
    organizer_agent_id: uuid.UUID | None = Field(
        default=None, foreign_key="conversation_agent.id", ondelete="RESTRICT"
    )
    participant_ids: list[str] = Field(
        default_factory=list, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ConversationContextSnapshot(SQLModel, table=True):
    __tablename__ = "conversation_context_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "revision", name="uq_conversation_snapshot_revision"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE", index=True
    )
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="RESTRICT"
    )
    revision: int
    repository_refs: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    spec_refs: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    content_refs: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    content_digest: str = Field(max_length=64)
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ConversationMessage(SQLModel, table=True):
    __tablename__ = "conversation_message"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_message_sequence"
        ),
        UniqueConstraint(
            "conversation_id",
            "idempotency_key",
            name="uq_conversation_message_idempotency",
        ),
        Index("ix_conversation_message_status", "conversation_id", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE", index=True
    )
    sequence: int
    author_type: MessageAuthorType = Field(
        sa_type=SAEnum(
            MessageAuthorType,
            name="messageauthortype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    author_id: uuid.UUID | None = Field(default=None)
    target_type: MessageTargetType = Field(
        sa_type=SAEnum(
            MessageTargetType,
            name="messagetargettype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    target_agent_id: uuid.UUID | None = Field(default=None)
    context_snapshot_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="conversation_context_snapshot.id",
        ondelete="RESTRICT",
    )
    configuration_revision_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="conversation_configuration_revision.id",
        ondelete="RESTRICT",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    status: MessageStatus = Field(
        default=MessageStatus.QUEUED,
        sa_type=SAEnum(
            MessageStatus,
            name="conversationmessagestatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    task_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_task.id", ondelete="SET NULL"
    )
    reply_to_id: uuid.UUID | None = Field(
        default=None, foreign_key="conversation_message.id", ondelete="SET NULL"
    )
    idempotency_key: str = Field(max_length=255)
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(POSTGRES_JSON))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ConversationEvent(SQLModel, table=True):
    __tablename__ = "conversation_event"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_event_sequence"
        ),
        Index("ix_conversation_event_cursor", "conversation_id", "sequence"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE", index=True
    )
    sequence: int
    event_type: str = Field(max_length=64)
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ConversationAttachment(SQLModel, table=True):
    __tablename__ = "conversation_attachment"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "content_digest",
            name="uq_conversation_attachment_content",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE", index=True
    )
    message_id: uuid.UUID | None = Field(
        default=None, foreign_key="conversation_message.id", ondelete="CASCADE"
    )
    filename: str = Field(max_length=255)
    content_type: str = Field(max_length=255)
    size: int
    content_digest: str = Field(max_length=64)
    storage_ref: str = Field(max_length=1024)
    scan_status: AttachmentScanStatus = Field(
        default=AttachmentScanStatus.PENDING,
        sa_type=SAEnum(
            AttachmentScanStatus,
            name="attachmentscanstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    scan_details: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class AgentDelegation(SQLModel, table=True):
    __tablename__ = "agent_delegation"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "idempotency_key", name="uq_agent_delegation_idempotency"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE", index=True
    )
    source_message_id: uuid.UUID = Field(
        foreign_key="conversation_message.id", nullable=False, ondelete="CASCADE"
    )
    source_agent_id: uuid.UUID = Field(
        foreign_key="conversation_agent.id", nullable=False, ondelete="RESTRICT"
    )
    target_agent_id: uuid.UUID = Field(
        foreign_key="conversation_agent.id", nullable=False, ondelete="RESTRICT"
    )
    input_payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    result_payload: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON)
    )
    task_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_task.id", ondelete="SET NULL"
    )
    status: DelegationStatus = Field(
        default=DelegationStatus.QUEUED,
        sa_type=SAEnum(
            DelegationStatus,
            name="delegationstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    idempotency_key: str = Field(max_length=255)
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(POSTGRES_JSON))
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
