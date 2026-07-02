import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import EmailStr
from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


class NamespaceRole(str, Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"


class ProviderAuthType(str, Enum):
    API_KEY = "api_key"
    OAUTH_EXTERNAL = "oauth_external"
    OAUTH_DEVICE_CODE = "oauth_device_code"
    AWS_SDK = "aws_sdk"
    EXTERNAL_PROCESS = "external_process"
    COPILOT_TOKEN = "copilot_token"
    CUSTOM = "custom"


class ProviderValidationStatus(str, Enum):
    UNVERIFIED = "unverified"
    SUCCESS = "success"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class ProviderModelSourceType(str, Enum):
    DISCOVERED = "discovered"
    MANUAL = "manual"


class ProviderModelSyncStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    SYNC_FAILED = "sync_failed"


class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore[assignment]
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserNamespaceLink(SQLModel, table=True):
    __tablename__ = "user_namespace_link"
    __table_args__ = (UniqueConstraint("user_id", "namespace_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE"
    )
    role: NamespaceRole = Field(
        default=NamespaceRole.USER,
        sa_type=SAEnum(
            NamespaceRole,
            name="namespacerole",
            values_callable=lambda roles: [role.value for role in roles],
        ),
    )


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)
    namespaces: list["Namespace"] = Relationship(
        back_populates="users", link_model=UserNamespaceLink
    )


class NamespaceBase(SQLModel):
    name: str = Field(min_length=1, max_length=255, unique=True, index=True)
    code: str = Field(min_length=1, max_length=64, unique=True, index=True)
    is_active: bool = True


class NamespaceCreate(NamespaceBase):
    admin_user_id: uuid.UUID | None = None


class NamespaceUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    code: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None
    admin_user_id: uuid.UUID | None = None


class Namespace(NamespaceBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    users: list[User] = Relationship(
        back_populates="namespaces", link_model=UserNamespaceLink
    )


class LlmProviderConfigBase(SQLModel):
    config_name: str = Field(min_length=1, max_length=255)
    provider_slug: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=1024)
    enabled: bool = True
    extra_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )


class LlmProviderModelInput(SQLModel):
    model_id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class LlmProviderConfigCreate(LlmProviderConfigBase):
    secret_inputs: dict[str, str] = Field(default_factory=dict)
    manual_models: list[LlmProviderModelInput] = []
    enabled_model_ids: list[str] = []


class LlmProviderConfigUpdate(SQLModel):
    config_name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, min_length=1, max_length=1024)
    enabled: bool | None = None
    secret_inputs: dict[str, str] | None = None
    extra_config: dict[str, Any] | None = None
    manual_models: list[LlmProviderModelInput] | None = None
    enabled_model_ids: list[str] | None = None


class LlmProviderSyncModelsRequest(SQLModel):
    manual_models: list[LlmProviderModelInput] = []
    enabled_model_ids: list[str] = []


class LlmProviderConfig(SQLModel, table=True):
    __tablename__ = "llm_provider_config"
    __table_args__ = (UniqueConstraint("namespace_id", "config_name"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE"
    )
    config_name: str = Field(max_length=255)
    provider_slug: str = Field(max_length=128)
    provider_display_name: str = Field(max_length=255)
    auth_type: ProviderAuthType = Field(
        sa_type=SAEnum(
            ProviderAuthType,
            name="providerauthtype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    base_url: str = Field(max_length=1024)
    secret_ciphertext: str | None = Field(default=None)
    secret_masked: str | None = Field(default=None, max_length=255)
    extra_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    supports_health_check: bool = False
    supports_model_discovery: bool = False
    validation_status: ProviderValidationStatus = Field(
        default=ProviderValidationStatus.UNVERIFIED,
        sa_type=SAEnum(
            ProviderValidationStatus,
            name="providervalidationstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    validation_message: str | None = Field(default=None, max_length=1024)
    last_validated_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    enabled: bool = True
    created_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    updated_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    models: list["LlmProviderModel"] = Relationship(
        back_populates="provider_config", cascade_delete=True
    )


class LlmProviderModel(SQLModel, table=True):
    __tablename__ = "llm_provider_model"
    __table_args__ = (UniqueConstraint("provider_config_id", "model_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    provider_config_id: uuid.UUID = Field(
        foreign_key="llm_provider_config.id", nullable=False, ondelete="CASCADE"
    )
    model_id: str = Field(max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    source_type: ProviderModelSourceType = Field(
        sa_type=SAEnum(
            ProviderModelSourceType,
            name="providermodelsourcetype",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    is_enabled: bool = False
    sync_status: ProviderModelSyncStatus = Field(
        default=ProviderModelSyncStatus.ACTIVE,
        sa_type=SAEnum(
            ProviderModelSyncStatus,
            name="providermodelsyncstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    raw_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    last_synced_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    provider_config: LlmProviderConfig | None = Relationship(back_populates="models")


class LlmProviderCatalogField(SQLModel):
    name: str
    label: str
    required: bool = True
    placeholder: str | None = None
    help_text: str | None = None


class LlmProviderCatalogItem(SQLModel):
    provider_slug: str
    display_name: str
    description: str | None = None
    auth_type: ProviderAuthType
    default_base_url: str | None = None
    supports_health_check: bool
    supports_model_discovery: bool
    base_url_editable: bool
    secret_fields: list[LlmProviderCatalogField] = []
    extra_fields: list[LlmProviderCatalogField] = []


class LlmProviderCatalogPublic(SQLModel):
    data: list[LlmProviderCatalogItem]
    count: int


class LlmProviderModelPublic(SQLModel):
    id: uuid.UUID
    model_id: str
    display_name: str | None = None
    source_type: ProviderModelSourceType
    is_enabled: bool
    sync_status: ProviderModelSyncStatus
    raw_metadata: dict[str, Any] = {}
    last_synced_at: datetime | None = None


class LlmProviderConfigPublic(SQLModel):
    id: uuid.UUID
    namespace_id: uuid.UUID
    config_name: str
    provider_slug: str
    provider_display_name: str
    auth_type: ProviderAuthType
    base_url: str
    secret_masked: str | None = None
    extra_config: dict[str, Any] = {}
    supports_health_check: bool
    supports_model_discovery: bool
    validation_status: ProviderValidationStatus
    validation_message: str | None = None
    last_validated_at: datetime | None = None
    enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    models: list[LlmProviderModelPublic] = []


class LlmProviderConfigsPublic(SQLModel):
    data: list[LlmProviderConfigPublic]
    count: int


class UserNamespaceAssignment(SQLModel):
    namespace_id: uuid.UUID
    role: NamespaceRole


class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None
    namespace_roles: list[UserNamespaceAssignment] = []


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


class NamespaceUserCreate(SQLModel):
    email: EmailStr = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    is_active: bool = True
    role: NamespaceRole = NamespaceRole.USER


class NamespaceUserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    role: NamespaceRole | None = None


class NamespacePublic(NamespaceBase):
    id: uuid.UUID
    created_at: datetime | None = None


class NamespacesPublic(SQLModel):
    data: list[NamespacePublic]
    count: int


class AdminUserCreate(UserCreate):
    namespace_assignments: list[UserNamespaceAssignment] = []


class AdminUserUpdate(UserUpdate):
    namespace_assignments: list[UserNamespaceAssignment] | None = None


class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


class ItemCreate(ItemBase):
    pass


class ItemUpdate(ItemBase):
    title: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore[assignment]


class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


class Message(SQLModel):
    message: str


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
