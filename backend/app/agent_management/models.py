import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from sqlalchemy import JSON, Column, Text, UniqueConstraint
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


class AgentStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AgentDefinition(SQLModel, table=True):
    __tablename__ = "agent_definition"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "slug", name="uq_agent_definition_namespace_slug"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: AgentStatus = Field(
        default=AgentStatus.ACTIVE,
        sa_type=SAEnum(
            AgentStatus,
            name="agentstatus",
            values_callable=lambda v: [x.value for x in v],
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


class AgentDraft(SQLModel, table=True):
    __tablename__ = "agent_draft"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    agent_id: uuid.UUID = Field(
        foreign_key="agent_definition.id",
        nullable=False,
        ondelete="CASCADE",
        unique=True,
    )
    revision: int = 1
    harness_profile_id: uuid.UUID | None = Field(
        default=None, foreign_key="harness_profile.id", ondelete="SET NULL"
    )
    provider_config_id: uuid.UUID | None = Field(
        default=None, foreign_key="llm_provider_config.id", ondelete="SET NULL"
    )
    model_id: str | None = Field(default=None, max_length=255)
    system_prompt: str = Field(default="", sa_column=Column(Text, nullable=False))
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    validated_revision: int | None = Field(default=None)
    validation_result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class HarnessProfile(SQLModel, table=True):
    __tablename__ = "harness_profile"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "name", name="uq_harness_profile_namespace_name"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=255)
    harness_type: str = Field(max_length=64, default="claude_code")
    config_schema_version: str = Field(max_length=32, default="1.0")
    cli_version_constraint: str = Field(default=">=1.0.0", max_length=128)
    sdk_version_constraint: str = Field(default=">=0.2.0", max_length=128)
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    archived: bool = Field(default=False)
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
