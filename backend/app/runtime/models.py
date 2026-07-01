import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from app.runtime.policy import TaskStatus


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
        UniqueConstraint("namespace_id", "runtime_type", name="uq_runtime_profile_namespace_platform"),
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
    created_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class AgentTask(SQLModel, table=True):
    __tablename__ = "agent_task"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(foreign_key="namespace.id", nullable=False, ondelete="CASCADE")
    session_id: uuid.UUID | None = Field(default=None, foreign_key="agent_session.id", ondelete="SET NULL")
    runtime_profile_id: uuid.UUID = Field(foreign_key="runtime_profile.id", nullable=False, ondelete="CASCADE")
    status: TaskStatus = Field(default=TaskStatus.QUEUED, sa_type=SAEnum(TaskStatus, name="agenttaskstatus", values_callable=lambda v: [x.value for x in v]))
    prompt: str
    snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    final_result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


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
