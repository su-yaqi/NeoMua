import uuid
from datetime import datetime, timezone

import jwt

from app.core.config import settings


class ArtifactDownloadTokenError(ValueError):
    pass


def issue_artifact_download_token(
    *, deployment_id: uuid.UUID, node_id: uuid.UUID, artifact_id: uuid.UUID,
    storage_key: str, expires_at: datetime,
) -> str:
    return jwt.encode(
        {
            "aud": "neomua-artifact-download",
            "deployment_id": str(deployment_id),
            "node_id": str(node_id),
            "artifact_id": str(artifact_id),
            "storage_key": storage_key,
            "exp": expires_at,
            "iat": datetime.now(timezone.utc),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def verify_artifact_download_token(token: str, deployment_id: uuid.UUID) -> dict:
    try:
        claims = jwt.decode(
            token, settings.SECRET_KEY, algorithms=["HS256"],
            audience="neomua-artifact-download",
        )
    except jwt.PyJWTError as exc:
        raise ArtifactDownloadTokenError("invalid or expired artifact download token") from exc
    if claims.get("deployment_id") != str(deployment_id):
        raise ArtifactDownloadTokenError("artifact download token scope mismatch")
    return claims
