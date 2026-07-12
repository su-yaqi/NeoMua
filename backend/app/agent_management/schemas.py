import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.agent_management.catalog import Diagnostic
from app.agent_management.service import TargetCompatibility, ValidationResult


class AgentCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = None  # "active" | "archived"


class DraftSave(BaseModel):
    expected_revision: int
    harness_profile_id: uuid.UUID | None = None
    provider_config_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, max_length=255)
    system_prompt: str | None = None
    config: dict | None = None


class AgentDraftPublic(BaseModel):
    agent_id: uuid.UUID
    revision: int
    harness_profile_id: uuid.UUID | None
    provider_config_id: uuid.UUID | None
    model_id: str | None
    system_prompt: str
    config: dict
    validated_revision: int | None
    validation_result: dict | None
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


class AgentsPublic(BaseModel):
    data: list[AgentListItem]
    count: int


class HarnessProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    harness_type: str = Field(default="claude_code", max_length=64)
    config_schema_version: str = Field(default="1.0", max_length=32)
    cli_version_constraint: str = Field(default=">=1.0.0", max_length=128)
    sdk_version_constraint: str = Field(default=">=0.2.0", max_length=128)
    config: dict = Field(default_factory=dict)


class HarnessProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    cli_version_constraint: str | None = Field(default=None, max_length=128)
    sdk_version_constraint: str | None = Field(default=None, max_length=128)
    config: dict | None = None
    archived: bool | None = None


class HarnessProfilePublic(BaseModel):
    id: uuid.UUID
    namespace_id: uuid.UUID
    name: str
    harness_type: str
    config_schema_version: str
    cli_version_constraint: str
    sdk_version_constraint: str
    config: dict
    archived: bool
    referenced_by_agents: bool
    created_at: datetime
    updated_at: datetime


class HarnessProfilesPublic(BaseModel):
    data: list[HarnessProfilePublic]
    count: int


class HarnessCatalogPublic(BaseModel):
    harnesses: list[dict]


class EnvironmentCatalogPublic(BaseModel):
    allowlist: list[str]
    reserved: list[str]
    denylist: list[str]
