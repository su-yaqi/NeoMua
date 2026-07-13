"""Non-browser CLI authentication and protocol capability endpoints."""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlmodel import col, select

from app import crud
from app.agent_management.capability_models import CliSession
from app.api.deps import CurrentUser, SessionDep
from app.core import security
from app.core.config import settings
from app.models import User

router = APIRouter(tags=["operator-cli"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CliLogin(StrictBody):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    client_name: str = Field(default="neomua-cli", min_length=1, max_length=255)


class CliRefresh(StrictBody):
    refresh_token: str = Field(min_length=32, max_length=512)


class CliLogout(CliRefresh):
    pass


def _token_hash(raw: str) -> str:
    key = hmac.new(
        settings.SECRET_KEY.encode(), b"neomua-cli-refresh-v1", hashlib.sha256
    ).digest()
    return hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()


def _issue(
    session: SessionDep,
    user: User,
    *,
    client_name: str,
    family_id: uuid.UUID | None = None,
    absolute_expires_at: datetime | None = None,
) -> tuple[str, CliSession]:
    now = datetime.now(timezone.utc)
    absolute = absolute_expires_at or now + timedelta(days=30)
    raw = secrets.token_urlsafe(48)
    row = CliSession(
        family_id=family_id or uuid.uuid4(),
        user_id=user.id,
        token_hash=_token_hash(raw),
        expires_at=absolute,
        absolute_expires_at=absolute,
        client_name=client_name,
    )
    session.add(row)
    session.flush()
    return raw, row


def _tokens(user: User, refresh_token: str, session: CliSession) -> dict[str, object]:
    return {
        "access_token": security.create_access_token(
            user.id, expires_delta=timedelta(minutes=15)
        ),
        "token_type": "bearer",
        "expires_in": 900,
        "refresh_token": refresh_token,
        "refresh_expires_at": session.absolute_expires_at,
    }


@router.post("/cli/login")
def cli_login(body: CliLogin, session: SessionDep) -> dict[str, object]:
    user = crud.authenticate(session=session, email=body.email, password=body.password)
    if user is None or not user.is_active:
        raise HTTPException(401, "Invalid credentials")
    raw, record = _issue(session, user, client_name=body.client_name)
    session.commit()
    return _tokens(user, raw, record)


@router.post("/cli/refresh")
def cli_refresh(body: CliRefresh, session: SessionDep) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    record = session.exec(
        select(CliSession)
        .where(CliSession.token_hash == _token_hash(body.refresh_token))
        .with_for_update()
    ).first()
    if record is None:
        raise HTTPException(401, "CLI refresh token is invalid")
    if record.replaced_by_id is not None:
        family = session.exec(
            select(CliSession).where(
                CliSession.family_id == record.family_id,
                col(CliSession.revoked_at).is_(None),
            )
        ).all()
        for item in family:
            item.revoked_at = now
            session.add(item)
        session.commit()
        raise HTTPException(
            401, "CLI refresh token reuse detected; session family revoked"
        )
    if (
        record.revoked_at is not None
        or record.expires_at <= now
        or record.absolute_expires_at <= now
    ):
        raise HTTPException(401, "CLI refresh session expired or revoked")
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "CLI refresh session user is unavailable")
    raw, replacement = _issue(
        session,
        user,
        client_name=record.client_name,
        family_id=record.family_id,
        absolute_expires_at=record.absolute_expires_at,
    )
    record.replaced_by_id = replacement.id
    record.last_used_at = now
    session.add(record)
    session.commit()
    return _tokens(user, raw, replacement)


@router.post("/cli/logout", status_code=204)
def cli_logout(body: CliLogout, session: SessionDep, current_user: CurrentUser) -> None:
    record = session.exec(
        select(CliSession)
        .where(
            CliSession.token_hash == _token_hash(body.refresh_token),
            CliSession.user_id == current_user.id,
        )
        .with_for_update()
    ).first()
    if record is None:
        return
    now = datetime.now(timezone.utc)
    family = session.exec(
        select(CliSession).where(
            CliSession.family_id == record.family_id,
            col(CliSession.revoked_at).is_(None),
        )
    ).all()
    for item in family:
        item.revoked_at = now
        session.add(item)
    session.commit()


@router.get("/capabilities")
def capabilities(_: CurrentUser) -> dict[str, object]:
    return {
        "api_protocol_version": "1.0",
        "cli_protocol_version": "1.0",
        "harnesses": ["claude_code"],
        "features": [
            "agents",
            "skills",
            "tools",
            "mcp",
            "plugins",
            "resolved_agent_spec",
            "releases",
            "activations",
            "tool_approvals",
            "operator_cli",
        ],
        "minimum_cli_version": "0.1.0",
    }
