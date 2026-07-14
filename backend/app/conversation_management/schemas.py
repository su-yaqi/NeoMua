import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.conversation_management.models import (
    ConversationMode,
    ConversationVisibility,
    MessageTargetType,
)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationAgentInput(StrictBody):
    runtime_agent_release_id: uuid.UUID


class ConversationCreate(StrictBody):
    title: str = Field(min_length=1, max_length=255)
    mode: ConversationMode
    runtime_id: uuid.UUID
    project_id: uuid.UUID | None = None
    visibility: ConversationVisibility = ConversationVisibility.PRIVATE
    provider_config_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, max_length=255)
    main_agent: ConversationAgentInput | None = None
    collaborators: list[ConversationAgentInput] = []

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "ConversationCreate":
        if self.mode == ConversationMode.CHAT:
            if self.provider_config_id is None or not self.model_id:
                raise ValueError("Chat requires provider_config_id and model_id")
            if self.main_agent is not None or self.collaborators:
                raise ValueError("Chat cannot include Agents")
        else:
            if self.main_agent is None:
                raise ValueError("Agent conversation requires a main Agent")
            if self.provider_config_id is not None or self.model_id is not None:
                raise ValueError("Agent model is fixed by its Release")
        if (
            self.visibility == ConversationVisibility.PROJECT
            and self.project_id is None
        ):
            raise ValueError("Project visibility requires a project")
        return self


class ConversationUpdate(StrictBody):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    archived: bool | None = None


class ConversationConfigurationUpdate(StrictBody):
    expected_revision: int = Field(ge=1)
    provider_config_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    participant_runtime_agent_release_ids: list[uuid.UUID] = []
    organizer_runtime_agent_release_id: uuid.UUID | None = None


class ConversationMessageCreate(StrictBody):
    content: str = Field(min_length=1)
    target_type: MessageTargetType
    target_agent_id: uuid.UUID | None = None
    attachment_ids: list[uuid.UUID] = []

    @model_validator(mode="after")
    def validate_target(self) -> "ConversationMessageCreate":
        if self.target_type == MessageTargetType.AGENT and self.target_agent_id is None:
            raise ValueError("target_agent_id is required for an Agent target")
        if (
            self.target_type != MessageTargetType.AGENT
            and self.target_agent_id is not None
        ):
            raise ValueError("target_agent_id is only valid for an Agent target")
        return self


class ConversationDerive(StrictBody):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    runtime_id: uuid.UUID
    provider_config_id: uuid.UUID
    model_id: str = Field(min_length=1, max_length=255)


class DelegationCreate(StrictBody):
    source_message_id: uuid.UUID
    source_conversation_agent_id: uuid.UUID
    target_conversation_agent_id: uuid.UUID
    content: str = Field(min_length=1)


class ContextRefresh(StrictBody):
    pass


class CatalogQuery(BaseModel):
    runtime_id: uuid.UUID | None = None


def message_payload(content: str, **extra: Any) -> dict[str, Any]:
    return {"content": content, **extra}
