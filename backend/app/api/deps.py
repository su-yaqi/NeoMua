import uuid
from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
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
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    user = session.get(User, token_data.sub)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
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
    if current_user.is_superuser:
        return current_namespace_id
    role = crud.get_namespace_role(
        session=session,
        user_id=current_user.id,
        namespace_id=current_namespace_id,
    )
    if role != NamespaceRole.ADMIN:
        raise HTTPException(status_code=403, detail="Namespace admin privilege required")
    return current_namespace_id
