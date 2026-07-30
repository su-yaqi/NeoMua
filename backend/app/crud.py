import uuid
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, col, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    Item,
    ItemCreate,
    LlmModelDefinition,
    LlmProviderConfig,
    LlmProviderModel,
    Namespace,
    NamespaceRole,
    NamespaceUserCreate,
    NamespaceUserUpdate,
    ProviderModelSyncStatus,
    ProviderValidationStatus,
    User,
    UserCreate,
    UserNamespaceAssignment,
    UserNamespaceLink,
    UserUpdate,
)


def _namespace_role_value(role: NamespaceRole | str) -> NamespaceRole:
    return role if isinstance(role, NamespaceRole) else NamespaceRole(role)


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


def get_user_namespace_links(
    *, session: Session, user_id: uuid.UUID
) -> list[UserNamespaceLink]:
    statement = select(UserNamespaceLink).where(UserNamespaceLink.user_id == user_id)
    return list(session.exec(statement).all())


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
        return list(session.exec(select(Namespace)).all())
    statement = (
        select(Namespace)
        .join(UserNamespaceLink, col(UserNamespaceLink.namespace_id) == Namespace.id)
        .where(UserNamespaceLink.user_id == user.id)
        .where(col(Namespace.is_active).is_(True))
    )
    return list(session.exec(statement).all())


def get_namespace(*, session: Session, namespace_id: uuid.UUID) -> Namespace | None:
    return session.get(Namespace, namespace_id)


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
    *,
    session: Session,
    user: User,
    namespace_id: uuid.UUID,
    user_in: NamespaceUserUpdate,
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
        select(User, col(UserNamespaceLink.role))
        .join(UserNamespaceLink, col(UserNamespaceLink.user_id) == User.id)
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


def list_llm_provider_configs(
    *, session: Session, namespace_id: uuid.UUID
) -> list[LlmProviderConfig]:
    statement = (
        select(LlmProviderConfig)
        .where(LlmProviderConfig.namespace_id == namespace_id)
        .order_by(col(LlmProviderConfig.created_at).desc())
    )
    return list(session.exec(statement).all())


def get_llm_provider_config(
    *, session: Session, config_id: uuid.UUID
) -> LlmProviderConfig | None:
    return session.get(LlmProviderConfig, config_id)


def create_llm_provider_config(
    *,
    session: Session,
    config: LlmProviderConfig,
) -> LlmProviderConfig:
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def update_llm_provider_config(
    *,
    session: Session,
    config: LlmProviderConfig,
) -> LlmProviderConfig:
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def replace_llm_provider_models(
    *,
    session: Session,
    config: LlmProviderConfig,
    models_payload: list[dict[str, Any]],
) -> list[LlmProviderModel]:
    existing = session.exec(
        select(LlmProviderModel).where(LlmProviderModel.provider_config_id == config.id)
    ).all()
    existing_by_id = {item.model_id: item for item in existing}
    retained: list[LlmProviderModel] = []
    seen: set[str] = set()
    for payload in models_payload:
        model_id = payload["model_id"]
        seen.add(model_id)
        definition = session.exec(
            select(LlmModelDefinition).where(
                LlmModelDefinition.namespace_id == config.namespace_id,
                LlmModelDefinition.provider_family == config.provider_slug.lower(),
                LlmModelDefinition.model_key == model_id,
            )
        ).first()
        if definition is None:
            definition = LlmModelDefinition(
                namespace_id=config.namespace_id,
                provider_family=config.provider_slug.lower(),
                model_key=model_id,
                display_name=payload.get("display_name") or model_id,
            )
            session.add(definition)
            session.flush()
        model = existing_by_id.get(model_id)
        if model is None:
            model = LlmProviderModel(
                provider_config_id=config.id,
                model_id=model_id,
                source_type=payload["source_type"],
                created_at=payload.get("created_at"),
            )
        model.model_definition_id = definition.id
        model.display_name = payload.get("display_name")
        model.source_type = payload["source_type"]
        model.is_enabled = payload["is_enabled"]
        model.sync_status = payload["sync_status"]
        model.raw_metadata = payload.get("raw_metadata", {})
        model.last_synced_at = payload.get("last_synced_at")
        model.updated_at = payload.get("updated_at")
        session.add(model)
        retained.append(model)
    for model in existing:
        if model.model_id in seen:
            continue
        model.is_enabled = False
        model.sync_status = ProviderModelSyncStatus.STALE
        model.updated_at = datetime.now(timezone.utc)
        session.add(model)
        retained.append(model)
    session.commit()
    session.refresh(config)
    return retained


def set_llm_provider_validation(
    *,
    session: Session,
    config: LlmProviderConfig,
    status: ProviderValidationStatus,
    message: str,
) -> LlmProviderConfig:
    config.validation_status = status
    config.validation_message = message
    config.last_validated_at = datetime.now(timezone.utc)
    session.add(config)
    session.commit()
    session.refresh(config)
    return config
