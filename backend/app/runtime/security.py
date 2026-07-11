import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Header, HTTPException

from app.core.config import settings

_SENSITIVE_EVENT_KEYS = {
    "api_key",
    "api_token",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "cookie",
    "set-cookie",
}


def redact_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower() in _SENSITIVE_EVENT_KEYS
            else redact_event_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_event_payload(item) for item in value]
    return value


def expected_internal_token() -> str:
    if settings.INTERNAL_RUNTIME_TOKEN:
        return settings.INTERNAL_RUNTIME_TOKEN
    return hmac.new(
        settings.SECRET_KEY.encode(), b"neomua-runtime-worker", hashlib.sha256
    ).hexdigest()


def require_internal_runtime(
    x_runtime_token: str | None = Header(default=None, alias="X-Runtime-Token"),
) -> None:
    if x_runtime_token is None or not hmac.compare_digest(
        x_runtime_token, expected_internal_token()
    ):
        raise HTTPException(403, "Invalid runtime service credential")


class GatewayScopeError(ValueError):
    pass


def issue_gateway_token(
    namespace_id: uuid.UUID,
    runtime_id: uuid.UUID,
    task_id: uuid.UUID,
    model_id: str,
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "aud": "neomua-model-gateway",
            "namespace_id": str(namespace_id),
            "runtime_id": str(runtime_id),
            "task_id": str(task_id),
            "model_id": model_id,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def verify_gateway_token(
    token: str,
    *,
    runtime_id: uuid.UUID,
    task_id: uuid.UUID,
    model_id: str,
) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            audience="neomua-model-gateway",
            leeway=settings.GATEWAY_JWT_LEEWAY_SECONDS,
            options={
                "require": [
                    "exp",
                    "iat",
                    "aud",
                    "namespace_id",
                    "runtime_id",
                    "task_id",
                    "model_id",
                ]
            },
        )
    except jwt.PyJWTError as exc:
        raise GatewayScopeError("invalid gateway token") from exc
    if (
        claims.get("runtime_id") != str(runtime_id)
        or claims.get("task_id") != str(task_id)
        or claims.get("model_id") != model_id
    ):
        raise GatewayScopeError("gateway token scope mismatch")
    return claims
