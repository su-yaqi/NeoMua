import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    Item,
    ItemCreate,
    Namespace,
    NamespaceRole,
    NamespaceUserCreate,
    NamespaceUserUpdate,
    User,
    UserCreate,
    UserNamespaceAssignment,
    UserNamespaceLink,
    UserUpdate,
)


def _namespace_role_value(role: NamespaceRole | str) -> str:
    return role.value if isinstance(role, NamespaceRole) else role


def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user


def get_user_by_email(*, session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    return session.exec(statement).first()


def get_user_namespace_links(*, session: Session, user_id: uuid.UUID) -> list[UserNamespaceLink]:
    statement = select(UserNamespaceLink).where(UserNamespaceLink.user_id == user_id)
    return session.exec(statement).all()


def set_user_namespace_assignments(
    *,
    session: Session,
    user: User,
    assignments: list[UserNamespaceAssignment],
) -> None:
    existing = get_user_namespace_links(session=session, user_id=user.id)
    for link in existing:
        session.delete(link)
    for assignment in assignments:
        link = UserNamespaceLink(
            user_id=user.id,
            namespace_id=assignment.namespace_id,
            role=_namespace_role_value(assignment.role),
        )
        session.add(link)
    session.commit()


def ensure_namespace_membership(
    *,
    session: Session,
    user_id: uuid.UUID,
    namespace_id: uuid.UUID,
    role: NamespaceRole,
) -> UserNamespaceLink:
    statement = select(UserNamespaceLink).where(
        UserNamespaceLink.user_id == user_id,
        UserNamespaceLink.namespace_id == namespace_id,
    )
    link = session.exec(statement).first()
    if link:
        link.role = _namespace_role_value(role)
    else:
        link = UserNamespaceLink(
            user_id=user_id,
            namespace_id=namespace_id,
            role=_namespace_role_value(role),
        )
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


def list_namespaces_for_user(*, session: Session, user: User) -> list[Namespace]:
    if user.is_superuser:
        return session.exec(select(Namespace)).all()
    statement = (
        select(Namespace)
        .join(UserNamespaceLink, UserNamespaceLink.namespace_id == Namespace.id)
        .where(UserNamespaceLink.user_id == user.id)
        .where(Namespace.is_active.is_(True))
    )
    return session.exec(statement).all()


def create_namespace_user(
    *, session: Session, namespace_id: uuid.UUID, user_in: NamespaceUserCreate
) -> User:
    user = get_user_by_email(session=session, email=user_in.email)
    if user is None:
        user = create_user(
            session=session,
            user_create=UserCreate(
                email=user_in.email,
                full_name=user_in.full_name,
                password=user_in.password,
                is_active=user_in.is_active,
                is_superuser=False,
            ),
        )
    ensure_namespace_membership(
        session=session,
        user_id=user.id,
        namespace_id=namespace_id,
        role=user_in.role,
    )
    return user


def update_namespace_user(
    *, session: Session, user: User, namespace_id: uuid.UUID, user_in: NamespaceUserUpdate
) -> User:
    user_data = user_in.model_dump(exclude_unset=True, exclude={"role"})
    extra_data = {}
    if "password" in user_data:
        extra_data["hashed_password"] = get_password_hash(user_data.pop("password"))
    user.sqlmodel_update(user_data, update=extra_data)
    session.add(user)
    session.commit()
    if user_in.role is not None:
        ensure_namespace_membership(
            session=session,
            user_id=user.id,
            namespace_id=namespace_id,
            role=user_in.role,
        )
    session.refresh(user)
    return user


def delete_namespace_membership(
    *, session: Session, user_id: uuid.UUID, namespace_id: uuid.UUID
) -> None:
    statement = select(UserNamespaceLink).where(
        UserNamespaceLink.user_id == user_id,
        UserNamespaceLink.namespace_id == namespace_id,
    )
    link = session.exec(statement).first()
    if link:
        session.delete(link)
        session.commit()


def get_namespace_role(
    *, session: Session, user_id: uuid.UUID, namespace_id: uuid.UUID
) -> NamespaceRole | None:
    statement = select(UserNamespaceLink).where(
        UserNamespaceLink.user_id == user_id,
        UserNamespaceLink.namespace_id == namespace_id,
    )
    link = session.exec(statement).first()
    return link.role if link else None


def list_namespace_users(
    *, session: Session, namespace_id: uuid.UUID
) -> list[tuple[User, NamespaceRole]]:
    statement = (
        select(User, UserNamespaceLink.role)
        .join(UserNamespaceLink, UserNamespaceLink.user_id == User.id)
        .where(UserNamespaceLink.namespace_id == namespace_id)
        .order_by(col(User.created_at).desc())
    )
    return list(session.exec(statement).all())


DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


def authenticate(*, session: Session, email: str, password: str) -> User | None:
    db_user = get_user_by_email(session=session, email=email)
    if not db_user:
        verify_password(password, DUMMY_HASH)
        return None
    verified, updated_password_hash = verify_password(password, db_user.hashed_password)
    if not verified:
        return None
    if updated_password_hash:
        db_user.hashed_password = updated_password_hash
        session.add(db_user)
        session.commit()
        session.refresh(db_user)
    return db_user


def create_item(*, session: Session, item_in: ItemCreate, owner_id: uuid.UUID) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item
