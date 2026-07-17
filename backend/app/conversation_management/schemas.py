import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.conversation_management.models import (
    ConversationMode,
    ConversationVisibility,
    MessageTargetType,
)
from app.runtime.models import ModelSelectionMode


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSelectionInput(StrictBody):
    mode: ModelSelectionMode
    runtime_model_binding_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_model_selection(self) -> "ModelSelectionInput":
        if self.mode == ModelSelectionMode.EXACT and self.runtime_model_binding_id is None:
            raise ValueError("exact mode requires runtime_model_binding_id")
        if (
            self.mode == ModelSelectionMode.AGENT_PREFERENCE
            and self.runtime_model_binding_id is not None
        ):
            raise ValueError("agent_preference does not accept runtime_model_binding_id")
        return self


class ConversationAgentInput(StrictBody):
    runtime_agent_release_id: uuid.UUID
    model_selection: ModelSelectionInput | None = None


class ConversationCreate(StrictBody):
    title: str = Field(min_length=1, max_length=255)
    mode: ConversationMode
    runtime_id: uuid.UUID | None = None
    runtime_instance_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    visibility: ConversationVisibility = ConversationVisibility.PRIVATE
    provider_config_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, max_length=255)
    chat_model_selection: ModelSelectionInput | None = None
    main_agent: ConversationAgentInput | None = None
    collaborators: list[ConversationAgentInput] = []

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "ConversationCreate":
        legacy = self.runtime_id is not None
        current = self.runtime_instance_id is not None
        if legacy == current:
            raise ValueError("submit exactly one Runtime target")
        if self.mode == ConversationMode.CHAT:
            if legacy and (self.provider_config_id is None or not self.model_id):
                raise ValueError("Legacy Chat requires provider_config_id and model_id")
            if current and (
                self.chat_model_selection is None
                or self.chat_model_selection.mode != ModelSelectionMode.EXACT
            ):
                raise ValueError("v0.9 Chat requires an exact model selection")
            if self.main_agent is not None or self.collaborators:
                raise ValueError("Chat cannot include Agents")
        else:
            if self.main_agent is None:
                raise ValueError("Agent conversation requires a main Agent")
            if (
                self.provider_config_id is not None
                or self.model_id is not None
                or self.chat_model_selection is not None
            ):
                raise ValueError("Agent model is fixed by its Release")
            participants = [self.main_agent, *self.collaborators]
            if current and any(item.model_selection is None for item in participants):
                raise ValueError("v0.9 Agent participants require model selection")
            if legacy and any(item.model_selection is not None for item in participants):
                raise ValueError("Legacy Agent participants cannot select v0.9 models")
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
    chat_model_selection: ModelSelectionInput | None = None
    participant_selections: list[ConversationAgentInput] = []


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
    runtime_instance_id: uuid.UUID
    chat_model_selection: ModelSelectionInput

    @model_validator(mode="after")
    def validate_chat_selection(self) -> "ConversationDerive":
        if self.chat_model_selection.mode != ModelSelectionMode.EXACT:
            raise ValueError("Derived Chat requires an exact model selection")
        return self


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
