import os
import uuid

import jwt


class GatewayAuthError(ValueError):
    pass


def verify_gateway_scope(
    token: str,
    runtime_id: uuid.UUID | None,
    task_id: uuid.UUID,
    model_id: str,
) -> dict:
    key = os.getenv("GATEWAY_SIGNING_KEY")
    if not key:
        raise GatewayAuthError("gateway signing key is not configured")
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            audience="neomua-model-gateway",
            leeway=float(os.getenv("GATEWAY_JWT_LEEWAY_SECONDS", "30")),
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
        raise GatewayAuthError("invalid gateway token") from exc
    if (
        (runtime_id is not None and claims.get("runtime_id") != str(runtime_id))
        or claims.get("task_id") != str(task_id)
        or claims.get("model_id") != model_id
    ):
        raise GatewayAuthError("gateway token scope mismatch")
    return claims
