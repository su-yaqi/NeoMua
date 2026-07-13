import uuid
from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app import crud
from app.core import security
from app.core.config import settings
from app.core.db import engine
from app.models import NamespaceRole, TokenPayload, User

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token", auto_error=False
)


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str | None, Depends(reusable_oauth2)]


def get_current_user(
    request: Request,
    session: SessionDep,
    token: TokenDep,
    access_cookie: Annotated[str | None, Cookie(alias="neomua_access")] = None,
) -> User:
    credential = token or access_cookie
    if credential is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(
            credential, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
    user = session.get(User, token_data.sub)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    request.state.auth_source = "bearer" if token else "cookie"
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


def get_current_namespace_id(
    x_namespace_id: Annotated[str | None, Header(alias="X-Namespace-Id")] = None,
    namespace_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    if namespace_id:
        return namespace_id
    if x_namespace_id:
        return uuid.UUID(x_namespace_id)
    return None


def require_namespace_admin(
    session: SessionDep,
    current_user: CurrentUser,
    current_namespace_id: Annotated[
        uuid.UUID | None, Depends(get_current_namespace_id)
    ],
) -> uuid.UUID:
    if current_namespace_id is None:
        raise HTTPException(status_code=400, detail="namespace_id is required")
    namespace = crud.get_namespace(
        session=session,
        namespace_id=current_namespace_id,
    )
    if namespace is None:
        raise HTTPException(status_code=404, detail="Namespace not found")
    if current_user.is_superuser:
        return current_namespace_id
    if not namespace.is_active:
        raise HTTPException(status_code=409, detail="Namespace is inactive")
    role = crud.get_namespace_role(
        session=session,
        user_id=current_user.id,
        namespace_id=current_namespace_id,
    )
    if role != NamespaceRole.ADMIN:
        raise HTTPException(
            status_code=403, detail="Namespace admin privilege required"
        )
    return current_namespace_id


def require_namespace_runtime_user(
    session: SessionDep,
    current_user: CurrentUser,
    current_namespace_id: Annotated[
        uuid.UUID | None, Depends(get_current_namespace_id)
    ],
) -> uuid.UUID:
    if current_namespace_id is None:
        raise HTTPException(status_code=400, detail="namespace_id is required")
    namespace = crud.get_namespace(session=session, namespace_id=current_namespace_id)
    if namespace is None:
        raise HTTPException(status_code=404, detail="Namespace not found")
    if not namespace.is_active:
        raise HTTPException(status_code=409, detail="Namespace is inactive")
    if current_user.is_superuser:
        return current_namespace_id
    role = crud.get_namespace_role(
        session=session, user_id=current_user.id, namespace_id=current_namespace_id
    )
    if role not in {NamespaceRole.ADMIN, NamespaceRole.DEVELOPER}:
        raise HTTPException(
            status_code=403, detail="Runtime access requires admin or developer"
        )
    return current_namespace_id


def require_namespace_member(
    session: SessionDep,
    current_user: CurrentUser,
    current_namespace_id: Annotated[
        uuid.UUID | None, Depends(get_current_namespace_id)
    ],
) -> uuid.UUID:
    """Resolve an active namespace that the current user may read and use."""
    if current_namespace_id is None:
        raise HTTPException(status_code=400, detail="namespace_id is required")
    namespace = crud.get_namespace(session=session, namespace_id=current_namespace_id)
    if namespace is None:
        raise HTTPException(status_code=404, detail="Namespace not found")
    if not namespace.is_active:
        raise HTTPException(status_code=409, detail="Namespace is inactive")
    if current_user.is_superuser:
        return current_namespace_id
    role = crud.get_namespace_role(
        session=session,
        user_id=current_user.id,
        namespace_id=current_namespace_id,
    )
    if role is None:
        raise HTTPException(status_code=403, detail="Namespace membership required")
    return current_namespace_id


def require_namespace_manager(
    session: SessionDep,
    current_user: CurrentUser,
    current_namespace_id: Annotated[
        uuid.UUID | None, Depends(get_current_namespace_id)
    ],
) -> uuid.UUID:
    """Allow namespace admins and developers to manage project configuration."""
    namespace_id = require_namespace_member(
        session=session,
        current_user=current_user,
        current_namespace_id=current_namespace_id,
    )
    if current_user.is_superuser:
        return namespace_id
    role = crud.get_namespace_role(
        session=session, user_id=current_user.id, namespace_id=namespace_id
    )
    if role not in {NamespaceRole.ADMIN, NamespaceRole.DEVELOPER}:
        raise HTTPException(
            status_code=403,
            detail="Namespace admin or developer privilege required",
        )
    return namespace_id
