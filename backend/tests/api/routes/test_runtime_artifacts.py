import io
import uuid
import zipfile

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import NamespaceRole
from app.runtime.artifacts.security import issue_artifact_download_token
from app.runtime.models import (
    ArtifactDeployment,
    ArtifactRelease,
    RuntimeArtifact,
    RuntimeNode,
)
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.user import authentication_token_from_email, create_random_user


def _zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("demo/SKILL.md", "# Demo")
    return output.getvalue()


def test_admin_uploads_immutable_signed_artifact(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ARTIFACT_LOCAL_ROOT", str(tmp_path))
    namespace = create_namespace(db)
    response = client.post(
        f"{settings.API_V1_STR}/runtime-artifacts",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        data={"kind": "skill", "logical_target": "skills", "version": "1.0.0"},
        files={"file": ("skill.zip", _zip(), "application/zip")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["content_sha256"]) == 64
    assert body["signature"]
    assert body["manifest"]["files"][0]["path"] == "demo/SKILL.md"


def test_developer_cannot_upload_artifact(client: TestClient, db: Session) -> None:
    namespace = create_namespace(db)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=developer.id,
        namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    response = client.post(
        f"{settings.API_V1_STR}/runtime-artifacts",
        headers=headers,
        data={"kind": "skill", "logical_target": "skills", "version": "1"},
        files={"file": ("skill.zip", _zip(), "application/zip")},
    )
    assert response.status_code == 403


def test_admin_releases_and_download_token_is_deployment_scoped(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ARTIFACT_LOCAL_ROOT", str(tmp_path))
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    archive_bytes = _zip()
    artifact = client.post(
        f"{settings.API_V1_STR}/runtime-artifacts",
        headers=headers,
        data={"kind": "skill", "logical_target": "skills", "version": "2.0.0"},
        files={"file": ("skill.zip", archive_bytes, "application/zip")},
    ).json()
    node = RuntimeNode(
        namespace_id=namespace.id,
        name="node",
        hostname="host",
        os_name="linux",
        architecture="arm64",
        agent_version="1",
        public_key="key",
        key_fingerprint="d" * 64,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    released = client.post(
        f"{settings.API_V1_STR}/runtime-artifacts/releases",
        headers=headers,
        json={
            "artifact_id": artifact["id"],
            "node_ids": [str(node.id)],
            "valid_for_seconds": 3600,
        },
    )
    assert released.status_code == 201
    deployment_id = uuid.UUID(released.json()["deployments"][0]["id"])
    deployment = db.get(ArtifactDeployment, deployment_id)
    release = db.get(ArtifactRelease, deployment.release_id)
    stored = db.get(RuntimeArtifact, deployment.artifact_id)
    token = issue_artifact_download_token(
        deployment_id=deployment.id,
        node_id=node.id,
        artifact_id=stored.id,
        storage_key=stored.storage_key,
        expires_at=release.valid_until,
    )
    downloaded = client.get(
        f"{settings.API_V1_STR}/node/artifacts/{deployment.id}/download",
        params={"token": token},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == archive_bytes
