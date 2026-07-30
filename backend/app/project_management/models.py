import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from sqlalchemy import JSON, Column, Index, Text, UniqueConstraint, text
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


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RepositoryStatus(str, Enum):
    UNVALIDATED = "unvalidated"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class SpecLocationType(str, Enum):
    DIRECTORY = "directory"
    FILE = "file"


class SpecLocationStatus(str, Enum):
    PENDING_INITIALIZATION = "pending_initialization"
    VALID = "valid"
    UNAVAILABLE = "unavailable"


class SpecScopeType(str, Enum):
    PLATFORM = "platform"
    NAMESPACE = "namespace"


class SpecVersionStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class SpecBindingStatus(str, Enum):
    PENDING = "pending"
    VALID = "valid"
    CONFLICT = "conflict"


class Project(SQLModel, table=True):
    __tablename__ = "project"
    __table_args__ = (
        UniqueConstraint("namespace_id", "slug", name="uq_project_namespace_slug"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, sa_column=Column(Text))
    status: ProjectStatus = Field(
        default=ProjectStatus.ACTIVE,
        sa_type=SAEnum(
            ProjectStatus,
            name="projectstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    default_runtime_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_profile.id", ondelete="SET NULL"
    )
    default_runtime_instance_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_instance.id", ondelete="SET NULL"
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
    archived_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ProjectMember(SQLModel, table=True):
    __tablename__ = "project_member"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ProjectRepository(SQLModel, table=True):
    __tablename__ = "project_repository"
    __table_args__ = (
        UniqueConstraint("project_id", "remote_url", name="uq_project_repository_url"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    remote_url: str = Field(max_length=2048)
    purpose: str = Field(sa_column=Column(Text, nullable=False))
    default_branch: str | None = Field(default=None, max_length=255)
    credential_ref: str | None = Field(default=None, max_length=512)
    runtime_workspace_refs: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    status: RepositoryStatus = Field(
        default=RepositoryStatus.UNVALIDATED,
        sa_type=SAEnum(
            RepositoryStatus,
            name="repositorystatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    validation_error: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON)
    )
    validated_commit: str | None = Field(default=None, max_length=64)
    validation_job_id: uuid.UUID | None = Field(
        default=None, foreign_key="runtime_job.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ProjectSpecLocation(SQLModel, table=True):
    __tablename__ = "project_spec_location"
    __table_args__ = (
        UniqueConstraint("repository_id", "path", name="uq_project_spec_location_path"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    repository_id: uuid.UUID = Field(
        foreign_key="project_repository.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    path: str = Field(max_length=1024)
    location_type: SpecLocationType = Field(
        sa_type=SAEnum(
            SpecLocationType,
            name="speclocationtype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    description: str = Field(sa_column=Column(Text, nullable=False))
    status: SpecLocationStatus = Field(
        default=SpecLocationStatus.PENDING_INITIALIZATION,
        sa_type=SAEnum(
            SpecLocationStatus,
            name="speclocationstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class SpecStandard(SQLModel, table=True):
    __tablename__ = "spec_standard"
    __table_args__ = (
        UniqueConstraint(
            "scope_type", "namespace_id", "slug", name="uq_spec_standard_scope_slug"
        ),
        Index(
            "uq_spec_standard_platform_slug",
            "slug",
            unique=True,
            postgresql_where=text("scope_type = 'platform' AND namespace_id IS NULL"),
            sqlite_where=text("scope_type = 'platform' AND namespace_id IS NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scope_type: SpecScopeType = Field(
        sa_type=SAEnum(
            SpecScopeType,
            name="specscopetype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    namespace_id: uuid.UUID | None = Field(
        default=None, foreign_key="namespace.id", ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, sa_column=Column(Text))
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class SpecStandardVersion(SQLModel, table=True):
    __tablename__ = "spec_standard_version"
    __table_args__ = (
        UniqueConstraint("standard_id", "version", name="uq_spec_standard_version"),
        UniqueConstraint(
            "standard_id", "content_digest", name="uq_spec_standard_digest"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    standard_id: uuid.UUID = Field(
        foreign_key="spec_standard.id", nullable=False, ondelete="RESTRICT", index=True
    )
    version: str = Field(max_length=64)
    manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(POSTGRES_JSON, nullable=False)
    )
    content_digest: str = Field(max_length=64)
    storage_ref: str | None = Field(default=None, max_length=1024)
    status: SpecVersionStatus = Field(
        default=SpecVersionStatus.ACTIVE,
        sa_type=SAEnum(
            SpecVersionStatus,
            name="specversionstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )


class ProjectSpecBinding(SQLModel, table=True):
    __tablename__ = "project_spec_binding"
    __table_args__ = (
        UniqueConstraint("spec_location_id", name="uq_project_spec_binding_location"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    spec_location_id: uuid.UUID = Field(
        foreign_key="project_spec_location.id", nullable=False, ondelete="CASCADE"
    )
    standard_version_id: uuid.UUID = Field(
        foreign_key="spec_standard_version.id", nullable=False, ondelete="RESTRICT"
    )
    status: SpecBindingStatus = Field(
        default=SpecBindingStatus.PENDING,
        sa_type=SAEnum(
            SpecBindingStatus,
            name="specbindingstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    validated_commit: str | None = Field(default=None, max_length=64)
    diff_preview: dict[str, Any] | None = Field(
        default=None, sa_column=Column(POSTGRES_JSON)
    )
    updated_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True)
    )
