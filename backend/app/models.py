import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import EmailStr
from sqlalchemy import DateTime, Enum as SAEnum, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


class NamespaceRole(str, Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"


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
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, ondelete="CASCADE")
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
    users: list[User] = Relationship(back_populates="namespaces", link_model=UserNamespaceLink)


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
