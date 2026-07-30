import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_management.service import TargetCompatibility


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentCreate(StrictRequest):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class AgentCompleteCreate(AgentCreate):
    harness_profile_id: uuid.UUID | None = None
    provider_config_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    preferred_model_definition_id: uuid.UUID | None = None
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = ""
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution_schema(self) -> "AgentCompleteCreate":
        if any(
            value is not None
            for value in (
                self.harness_profile_id,
                self.provider_config_id,
                self.model_id,
            )
        ):
            raise ValueError("Legacy Harness/model fields are read-only in v0.9")
        if self.preferred_model_definition_id is None:
            raise ValueError("preferred_model_definition_id is required")
        return self


class AgentCopy(StrictRequest):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)


class AgentUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = None  # "active" | "archived"


class DraftSave(StrictRequest):
    expected_revision: int
    harness_profile_id: uuid.UUID | None = None
    provider_config_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, max_length=255)
    preferred_model_definition_id: uuid.UUID | None = None
    execution_policy: dict[str, Any] | None = None
    system_prompt: str | None = None
    config: dict[str, Any] | None = None


class AgentDraftPublic(BaseModel):
    agent_id: uuid.UUID
    revision: int
    harness_profile_id: uuid.UUID | None
    provider_config_id: uuid.UUID | None
    model_id: str | None
    preferred_model_definition_id: uuid.UUID | None
    execution_policy: dict[str, Any]
    system_prompt: str
    config: dict[str, Any]
    validated_revision: int | None
    validation_result: dict[str, Any] | None
    validation_status: str
    updated_at: datetime


class AgentPublic(BaseModel):
    id: uuid.UUID
    namespace_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class AgentListItem(AgentPublic):
    draft_revision: int
    validation_status: str
    harness_type: str | None = None
    model_id: str | None = None
    preferred_model_definition_id: uuid.UUID | None = None


class AgentsPublic(BaseModel):
    data: list[AgentListItem]
    count: int


class HarnessProfileCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=255)
    harness_type: str = Field(default="claude_code", max_length=64)
    config_schema_version: str = Field(default="1.0", max_length=32)
    cli_version_constraint: str = Field(default=">=1.0.0", max_length=128)
    sdk_version_constraint: str = Field(default=">=0.2.0", max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)


class HarnessProfileUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    cli_version_constraint: str | None = Field(default=None, max_length=128)
    sdk_version_constraint: str | None = Field(default=None, max_length=128)
    config: dict[str, Any] | None = None
    archived: bool | None = None


class HarnessProfilePublic(BaseModel):
    id: uuid.UUID
    namespace_id: uuid.UUID
    name: str
    harness_type: str
    config_schema_version: str
    cli_version_constraint: str
    sdk_version_constraint: str
    config: dict[str, Any]
    archived: bool
    referenced_by_agents: bool
    target_compatibility: list[TargetCompatibility]
    created_at: datetime
    updated_at: datetime


class HarnessProfilesPublic(BaseModel):
    data: list[HarnessProfilePublic]
    count: int


class HarnessCatalogPublic(BaseModel):
    harnesses: list[dict[str, Any]]


class EnvironmentCatalogPublic(BaseModel):
    allowlist: list[str]
    reserved: list[str]
    denylist: list[str]
