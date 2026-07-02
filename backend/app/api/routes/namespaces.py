import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, delete, func, select

from app import crud
from app.api.deps import (
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
    require_namespace_admin,
)
from app.models import (
    AdminUserCreate,
    AdminUserUpdate,
    Message,
    Namespace,
    NamespaceCreate,
    NamespacePublic,
    NamespaceRole,
    NamespacesPublic,
    NamespaceUpdate,
    NamespaceUserCreate,
    NamespaceUserUpdate,
    User,
    UserNamespaceAssignment,
    UserNamespaceLink,
    UserPublic,
    UsersPublic,
)

router = APIRouter(prefix="/namespaces", tags=["namespaces"])
platform_router = APIRouter(prefix="/platform", tags=["platform"])


def to_user_public(session: SessionDep, user: User) -> UserPublic:
    links = crud.get_user_namespace_links(session=session, user_id=user.id)
    return UserPublic(
        **user.model_dump(),
        namespace_roles=[
            UserNamespaceAssignment(namespace_id=link.namespace_id, role=link.role)
            for link in links
        ],
    )


@router.get("/mine", response_model=NamespacesPublic)
def read_my_namespaces(session: SessionDep, current_user: CurrentUser) -> Any:
    namespaces = crud.list_namespaces_for_user(session=session, user=current_user)
    return NamespacesPublic(
        data=[NamespacePublic.model_validate(namespace) for namespace in namespaces],
        count=len(namespaces),
    )


@router.get("/{namespace_id}/users", response_model=UsersPublic)
def read_namespace_users(
    namespace_id: uuid.UUID,
    session: SessionDep,
    _: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    entries = crud.list_namespace_users(session=session, namespace_id=namespace_id)
    users = []
    for user, role in entries:
        user_public = to_user_public(session, user)
        user_public.namespace_roles = [
            UserNamespaceAssignment(namespace_id=namespace_id, role=role)
        ]
        users.append(user_public)
    return UsersPublic(data=users, count=len(users))


@router.post("/{namespace_id}/users", response_model=UserPublic)
def create_namespace_user(
    namespace_id: uuid.UUID,
    session: SessionDep,
    user_in: NamespaceUserCreate,
    _: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    namespace = session.get(Namespace, namespace_id)
    if not namespace:
        raise HTTPException(status_code=404, detail="Namespace not found")
    user = crud.create_namespace_user(
        session=session,
        namespace_id=namespace_id,
        user_in=user_in,
    )
    return to_user_public(session, user)


@router.patch("/{namespace_id}/users/{user_id}", response_model=UserPublic)
def update_namespace_user(
    namespace_id: uuid.UUID,
    user_id: uuid.UUID,
    session: SessionDep,
    user_in: NamespaceUserUpdate,
    current_user: CurrentUser,
    _: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    if (
        not current_user.is_superuser
        and user_id == current_user.id
        and user_in.role is not None
        and user_in.role != NamespaceRole.ADMIN
    ):
        raise HTTPException(
            status_code=403,
            detail="Namespace admins cannot change their own role",
        )
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user = crud.update_namespace_user(
        session=session,
        user=user,
        namespace_id=namespace_id,
        user_in=user_in,
    )
    return to_user_public(session, user)


@router.delete("/{namespace_id}/users/{user_id}", response_model=Message)
def delete_namespace_user(
    namespace_id: uuid.UUID,
    user_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    _: uuid.UUID = Depends(require_namespace_admin),
) -> Message:
    if not current_user.is_superuser and user_id == current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Namespace admins cannot remove themselves from the namespace",
        )
    crud.delete_namespace_membership(
        session=session,
        user_id=user_id,
        namespace_id=namespace_id,
    )
    return Message(message="User removed from namespace successfully")


@platform_router.get(
    "/namespaces",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=NamespacesPublic,
)
def read_namespaces(session: SessionDep, skip: int = 0, limit: int = 100) -> Any:
    count_statement = select(func.count()).select_from(Namespace)
    count = session.exec(count_statement).one()
    statement = (
        select(Namespace)
        .order_by(col(Namespace.created_at).desc())
        .offset(skip)
        .limit(limit)
    )
    namespaces = session.exec(statement).all()
    return NamespacesPublic(
        data=[NamespacePublic.model_validate(namespace) for namespace in namespaces],
        count=count,
    )


@platform_router.post(
    "/namespaces",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=NamespacePublic,
)
def create_namespace(*, session: SessionDep, namespace_in: NamespaceCreate) -> Any:
    existing = session.exec(
        select(Namespace).where(Namespace.code == namespace_in.code)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Namespace code already exists")
    namespace = Namespace.model_validate(
        namespace_in.model_dump(exclude={"admin_user_id"})
    )
    session.add(namespace)
    session.commit()
    session.refresh(namespace)
    if namespace_in.admin_user_id:
        crud.ensure_namespace_membership(
            session=session,
            user_id=namespace_in.admin_user_id,
            namespace_id=namespace.id,
            role=NamespaceRole.ADMIN,
        )
    return namespace


@platform_router.patch(
    "/namespaces/{namespace_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=NamespacePublic,
)
def update_namespace(
    namespace_id: uuid.UUID,
    session: SessionDep,
    namespace_in: NamespaceUpdate,
) -> Any:
    namespace = session.get(Namespace, namespace_id)
    if not namespace:
        raise HTTPException(status_code=404, detail="Namespace not found")
    namespace.sqlmodel_update(
        namespace_in.model_dump(exclude_unset=True, exclude={"admin_user_id"})
    )
    session.add(namespace)
    session.commit()
    session.refresh(namespace)
    if namespace_in.admin_user_id:
        crud.ensure_namespace_membership(
            session=session,
            user_id=namespace_in.admin_user_id,
            namespace_id=namespace.id,
            role=NamespaceRole.ADMIN,
        )
    return namespace


@platform_router.delete(
    "/namespaces/{namespace_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=Message,
)
def delete_namespace(namespace_id: uuid.UUID, session: SessionDep) -> Message:
    namespace = session.get(Namespace, namespace_id)
    if not namespace:
        raise HTTPException(status_code=404, detail="Namespace not found")
    links = select(UserNamespaceLink).where(
        UserNamespaceLink.namespace_id == namespace_id
    )
    for link in session.exec(links).all():
        session.delete(link)
    session.delete(namespace)
    session.commit()
    return Message(message="Namespace deleted successfully")


@platform_router.get(
    "/users",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UsersPublic,
)
def read_platform_users(session: SessionDep, skip: int = 0, limit: int = 100) -> Any:
    count_statement = select(func.count()).select_from(User)
    count = session.exec(count_statement).one()
    users = session.exec(
        select(User).order_by(col(User.created_at).desc()).offset(skip).limit(limit)
    ).all()
    return UsersPublic(
        data=[to_user_public(session, user) for user in users], count=count
    )


@platform_router.post(
    "/users",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
)
def create_platform_user(*, session: SessionDep, user_in: AdminUserCreate) -> Any:
    if crud.get_user_by_email(session=session, email=user_in.email):
        raise HTTPException(status_code=400, detail="Email already exists")
    user = crud.create_user(session=session, user_create=user_in)
    crud.set_user_namespace_assignments(
        session=session,
        user=user,
        assignments=user_in.namespace_assignments,
    )
    return to_user_public(session, user)


@platform_router.patch(
    "/users/{user_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
)
def update_platform_user(
    user_id: uuid.UUID,
    session: SessionDep,
    user_in: AdminUserUpdate,
) -> Any:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
    user = crud.update_user(session=session, db_user=user, user_in=user_in)
    if user_in.namespace_assignments is not None:
        crud.set_user_namespace_assignments(
            session=session,
            user=user,
            assignments=user_in.namespace_assignments,
        )
    return to_user_public(session, user)


@platform_router.delete(
    "/users/{user_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=Message,
)
def delete_platform_user(
    user_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Message:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=403, detail="Cannot delete yourself")
    session.exec(delete(User).where(User.id == user_id))
    session.commit()
    return Message(message="User deleted successfully")
