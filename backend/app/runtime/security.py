import hashlib
import hmac

from fastapi import Header, HTTPException

from app.core.config import settings


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
