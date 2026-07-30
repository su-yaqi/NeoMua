import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.project_management.models import (
    SpecLocationType,
    SpecScopeType,
)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectInitialRepositoryCreate(StrictBody):
    remote_url: str = Field(min_length=1, max_length=2048)
    purpose: str = Field(min_length=1)
    default_branch: str | None = Field(default=None, max_length=255)
    credential_ref: str | None = Field(default=None, max_length=512)


class ProjectCreate(StrictBody):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    default_runtime_instance_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] = Field(default_factory=list)
    initial_repositories: list[ProjectInitialRepositoryCreate] = Field(
        default_factory=list, max_length=10
    )


class ProjectUpdate(StrictBody):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    default_runtime_instance_id: uuid.UUID | None = None
    archive: bool | None = None


class ProjectMembersUpdate(StrictBody):
    user_ids: list[uuid.UUID]


class RepositoryCreate(StrictBody):
    remote_url: str = Field(min_length=1, max_length=2048)
    purpose: str = Field(min_length=1)
    default_branch: str | None = Field(default=None, max_length=255)
    credential_ref: str | None = Field(default=None, max_length=512)


class RepositoryUpdate(StrictBody):
    remote_url: str | None = Field(default=None, min_length=1, max_length=2048)
    purpose: str | None = Field(default=None, min_length=1)
    default_branch: str | None = Field(default=None, max_length=255)
    credential_ref: str | None = Field(default=None, max_length=512)


class RepositoryValidationRequest(StrictBody):
    runtime_instance_id: uuid.UUID


class SpecLocationCreate(StrictBody):
    repository_id: uuid.UUID
    path: str = Field(min_length=1, max_length=1024)
    location_type: SpecLocationType
    description: str = Field(min_length=1)


class SpecLocationUpdate(StrictBody):
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    location_type: SpecLocationType | None = None
    description: str | None = Field(default=None, min_length=1)


class SpecStandardCreate(StrictBody):
    scope_type: SpecScopeType
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "SpecStandardCreate":
        return self


class SpecStandardVersionCreate(StrictBody):
    version: str = Field(min_length=1, max_length=64)
    manifest: dict[str, Any]
    content_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    storage_ref: str | None = Field(default=None, max_length=1024)


class SpecStandardCompleteCreate(SpecStandardCreate):
    version: str = Field(min_length=1, max_length=64)
    manifest: dict[str, Any]
    storage_ref: str | None = Field(default=None, max_length=1024)


class SpecVersionDeprecate(StrictBody):
    deprecated: bool = True


class SpecBindingUpdate(StrictBody):
    standard_version_id: uuid.UUID
