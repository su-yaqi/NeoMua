import os
import uuid

import jwt


class GatewayAuthError(ValueError):
    pass


def verify_gateway_scope(
    token: str, runtime_id: uuid.UUID | None, model_id: str
) -> dict:
    key = os.getenv("GATEWAY_SIGNING_KEY")
    if not key:
        raise GatewayAuthError("gateway signing key is not configured")
    try:
        claims = jwt.decode(
            token, key, algorithms=["HS256"], audience="neomua-model-gateway"
        )
    except jwt.PyJWTError as exc:
        raise GatewayAuthError("invalid gateway token") from exc
    if (
        runtime_id is not None and claims.get("runtime_id") != str(runtime_id)
    ) or claims.get("model_id") != model_id:
        raise GatewayAuthError("gateway token scope mismatch")
    return claims
