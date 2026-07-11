import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, col, select

from app.core.config import settings
from app.models import RefreshSession, User


class RefreshTokenError(ValueError):
    pass


def _hash_refresh_token(token: str) -> str:
    key = hmac.new(
        settings.SECRET_KEY.encode(), b"neomua-refresh-session", hashlib.sha256
    ).digest()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def issue_refresh_session(
    session: Session,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    ttl: timedelta = timedelta(days=8),
) -> tuple[str, RefreshSession]:
    raw = f"nmr_{secrets.token_urlsafe(48)}"
    record = RefreshSession(
        family_id=family_id or uuid.uuid4(),
        user_id=user.id,
        token_hash=_hash_refresh_token(raw),
        expires_at=datetime.now(timezone.utc) + ttl,
    )
    session.add(record)
    session.flush()
    return raw, record


def rotate_refresh_session(
    session: Session, raw_token: str
) -> tuple[User, str, RefreshSession]:
    now = datetime.now(timezone.utc)
    record = session.exec(
        select(RefreshSession)
        .where(RefreshSession.token_hash == _hash_refresh_token(raw_token))
        .with_for_update()
    ).first()
    if record is None:
        raise RefreshTokenError("refresh token is invalid")
    if record.revoked_at is not None:
        if record.replaced_by_id is not None:
            family = session.exec(
                select(RefreshSession).where(
                    RefreshSession.family_id == record.family_id,
                    col(RefreshSession.revoked_at).is_(None),
                )
            ).all()
            for member in family:
                member.revoked_at = now
                session.add(member)
            session.commit()
        raise RefreshTokenError("refresh token reuse detected")
    if record.expires_at <= now:
        record.revoked_at = now
        session.add(record)
        session.commit()
        raise RefreshTokenError("refresh token expired")
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        record.revoked_at = now
        session.add(record)
        session.commit()
        raise RefreshTokenError("refresh session user is unavailable")
    raw, replacement = issue_refresh_session(
        session, user, family_id=record.family_id
    )
    record.revoked_at = now
    record.last_used_at = now
    record.replaced_by_id = replacement.id
    session.add(record)
    session.commit()
    return user, raw, replacement


def revoke_refresh_session(session: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    record = session.exec(
        select(RefreshSession)
        .where(RefreshSession.token_hash == _hash_refresh_token(raw_token))
        .with_for_update()
    ).first()
    if record and record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        session.add(record)
        session.commit()
