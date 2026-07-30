import hashlib
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.core.config import settings
from app.runtime.artifacts.local_storage import LocalArtifactStorage
from app.runtime.artifacts.manifest import (
    MAX_TOTAL_BYTES,
    ArtifactFile,
    ArtifactKind,
    ArtifactManifest,
    LogicalTarget,
)
from app.runtime.artifacts.security import (
    ArtifactDownloadTokenError,
    verify_artifact_download_token,
)
from app.runtime.artifacts.service import ArtifactReleaseService
from app.runtime.artifacts.signing import ArtifactSigner, configured_artifact_signer
from app.runtime.artifacts.storage import ArtifactStorage
from app.runtime.models import (
    ArtifactDeployment,
    ArtifactRelease,
    DeploymentStatus,
    RuntimeArtifact,
    RuntimeNode,
    RuntimeNodeArtifact,
)

router = APIRouter(prefix="/runtime-artifacts", tags=["runtime-artifacts"])
node_router = APIRouter(prefix="/node/artifacts", tags=["node-artifacts"])
_UPLOAD_LOCK_BASE = 0x4E4D5500


def _acquire_upload_slot(session: SessionDep) -> int:
    for slot in range(settings.ARTIFACT_MAX_CONCURRENT_UPLOADS):
        lock_id = _UPLOAD_LOCK_BASE + slot
        if (
            session.connection()
            .execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
            )
            .scalar_one()
        ):
            return lock_id
    raise HTTPException(429, "Artifact upload concurrency limit reached")


def _release_upload_slot(session: SessionDep, lock_id: int) -> None:
    session.connection().execute(
        text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
    )


class ArtifactPublic(BaseModel):
    id: uuid.UUID
    kind: ArtifactKind
    logical_target: LogicalTarget
    version: str
    content_sha256: str
    size: int
    manifest: dict[str, Any]
    signature: str
    signing_public_key: str


class ArtifactsPublic(BaseModel):
    data: list[ArtifactPublic]
    count: int


class ReleaseCreate(BaseModel):
    artifact_id: uuid.UUID
    node_ids: list[uuid.UUID] = Field(min_length=1)
    valid_for_seconds: int = Field(default=3600, ge=60, le=86400)


class DeploymentPublic(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    artifact_id: uuid.UUID
    previous_artifact_id: uuid.UUID | None
    attempt: int
    status: DeploymentStatus
    error: dict[str, Any] | None


class ReleasePublic(BaseModel):
    id: uuid.UUID
    artifact_id: uuid.UUID
    valid_until: datetime
    rollback_of_release_id: uuid.UUID | None
    deployments: list[DeploymentPublic]


def _public(artifact: RuntimeArtifact) -> ArtifactPublic:
    return ArtifactPublic(
        id=artifact.id,
        kind=artifact.kind,
        logical_target=artifact.logical_target,
        version=artifact.version,
        content_sha256=artifact.content_sha256,
        size=artifact.size,
        manifest=artifact.manifest,
        signature=artifact.signature,
        signing_public_key=artifact.signing_public_key,
    )


def artifact_storage() -> ArtifactStorage:
    if settings.ARTIFACT_STORAGE_BACKEND == "local":
        return LocalArtifactStorage(Path(settings.ARTIFACT_LOCAL_ROOT))
    if settings.ARTIFACT_STORAGE_BACKEND == "s3":
        if not settings.ARTIFACT_S3_BUCKET:
            raise RuntimeError("ARTIFACT_S3_BUCKET is required for S3 storage")
        from app.runtime.artifacts.s3_storage import S3ArtifactStorage

        return S3ArtifactStorage(
            bucket=settings.ARTIFACT_S3_BUCKET,
            endpoint_url=settings.ARTIFACT_S3_ENDPOINT,
            access_key=settings.ARTIFACT_S3_ACCESS_KEY,
            secret_key=settings.ARTIFACT_S3_SECRET_KEY,
            region=settings.ARTIFACT_S3_REGION,
        )
    raise RuntimeError("Unsupported artifact storage backend")


def artifact_signer() -> ArtifactSigner:
    return configured_artifact_signer()


def _build_manifest(
    archive_path: Path,
    artifact_id: uuid.UUID,
    version: str,
    kind: ArtifactKind,
    logical_target: LogicalTarget,
) -> ArtifactManifest:
    files: list[ArtifactFile] = []
    total_size = 0
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise HTTPException(422, "Artifact must be a valid ZIP archive") from exc
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if len(files) >= 10_000:
                raise HTTPException(413, "Artifact file count exceeds limit")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise HTTPException(422, "Artifact symlink entries are forbidden")
            digest = hashlib.sha256()
            size = 0
            with archive.open(info, "r") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    total_size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(413, "Artifact file exceeds size limit")
                    if total_size > MAX_TOTAL_BYTES:
                        raise HTTPException(413, "Artifact expanded size exceeds limit")
                    digest.update(chunk)
            try:
                files.append(
                    ArtifactFile(
                        path=info.filename,
                        sha256=digest.hexdigest(),
                        size=size,
                        symlink=False,
                    )
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    try:
        return ArtifactManifest(
            artifact_id=str(artifact_id),
            version=version,
            kind=kind,
            logical_target=logical_target,
            files=files,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("", response_model=ArtifactPublic, status_code=201)
async def upload_artifact(
    session: SessionDep,
    current_user: CurrentUser,
    kind: ArtifactKind = Form(),
    logical_target: LogicalTarget = Form(),
    version: str = Form(min_length=1, max_length=128),
    file: UploadFile = File(),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> ArtifactPublic:
    lock_id = _acquire_upload_slot(session)
    temp_dir = Path(settings.ARTIFACT_TEMP_DIR or tempfile.gettempdir())
    temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    required_free = (
        settings.ARTIFACT_MAX_ARCHIVE_BYTES + settings.ARTIFACT_TEMP_MIN_FREE_BYTES
    )
    if shutil.disk_usage(temp_dir).free < required_free:
        _release_upload_slot(session, lock_id)
        raise HTTPException(507, "Artifact temporary storage capacity is insufficient")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="neomua-artifact-", suffix=".zip", dir=temp_dir
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.ARTIFACT_MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "Artifact archive exceeds size limit")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        artifact_id = uuid.uuid4()
        manifest = _build_manifest(
            temporary, artifact_id, version, kind, logical_target
        )
        content_sha256 = digest.hexdigest()
        storage_key = f"sha256/{content_sha256[:2]}/{content_sha256}"
        artifact_storage().put_once(storage_key, temporary)
        signer = artifact_signer()
        signature = signer.sign(manifest.canonical_bytes())
        artifact = RuntimeArtifact(
            id=artifact_id,
            namespace_id=namespace_id,
            kind=kind,
            logical_target=logical_target,
            version=version,
            content_sha256=content_sha256,
            storage_key=storage_key,
            size=size,
            manifest=manifest.model_dump(mode="json"),
            signature=signature,
            signing_public_key=signer.public_key(),
            created_by=current_user.id,
        )
        session.add(artifact)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "Artifact version already exists") from exc
        return _public(artifact)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
        _release_upload_slot(session, lock_id)


@router.get("", response_model=ArtifactsPublic)
def list_artifacts(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> ArtifactsPublic:
    artifacts = session.exec(
        select(RuntimeArtifact)
        .where(RuntimeArtifact.namespace_id == namespace_id)
        .order_by(col(RuntimeArtifact.created_at).desc())
    ).all()
    return ArtifactsPublic(
        data=[_public(artifact) for artifact in artifacts], count=len(artifacts)
    )


@router.get("/{artifact_id}", response_model=ArtifactPublic)
def read_artifact(
    artifact_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> ArtifactPublic:
    artifact = session.get(RuntimeArtifact, artifact_id)
    if artifact is None or artifact.namespace_id != namespace_id:
        raise HTTPException(404, "Artifact not found")
    return _public(artifact)


def _public_release(session: SessionDep, release: ArtifactRelease) -> ReleasePublic:
    deployments = session.exec(
        select(ArtifactDeployment)
        .where(ArtifactDeployment.release_id == release.id)
        .order_by(col(ArtifactDeployment.node_id), col(ArtifactDeployment.attempt))
    ).all()
    return ReleasePublic(
        id=release.id,
        artifact_id=release.artifact_id,
        valid_until=release.valid_until,
        rollback_of_release_id=release.rollback_of_release_id,
        deployments=[
            DeploymentPublic(
                id=item.id,
                node_id=item.node_id,
                artifact_id=item.artifact_id,
                previous_artifact_id=item.previous_artifact_id,
                attempt=item.attempt,
                status=item.status,
                error=item.error,
            )
            for item in deployments
        ],
    )


@router.post("/releases", response_model=ReleasePublic, status_code=201)
def create_release(
    body: ReleaseCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> ReleasePublic:
    artifact = session.get(RuntimeArtifact, body.artifact_id)
    if artifact is None or artifact.namespace_id != namespace_id:
        raise HTTPException(404, "Artifact not found")
    if len(body.node_ids) != len(set(body.node_ids)):
        raise HTTPException(400, "Duplicate release node")
    nodes = session.exec(
        select(RuntimeNode).where(col(RuntimeNode.id).in_(body.node_ids))
    ).all()
    if len(nodes) != len(body.node_ids) or any(
        node.namespace_id != namespace_id or node.revoked_at is not None
        for node in nodes
    ):
        raise HTTPException(404, "Active release node not found")
    release = ArtifactRelease(
        namespace_id=namespace_id,
        artifact_id=artifact.id,
        valid_until=datetime.now(timezone.utc)
        + timedelta(seconds=body.valid_for_seconds),
        created_by=current_user.id,
    )
    session.add(release)
    session.flush()
    for node in sorted(nodes, key=lambda item: str(item.id)):
        session.exec(
            select(RuntimeNode).where(RuntimeNode.id == node.id).with_for_update()
        ).one()
        active = session.exec(
            select(ArtifactDeployment).where(
                ArtifactDeployment.node_id == node.id,
                ArtifactDeployment.logical_target == artifact.logical_target,
                col(ArtifactDeployment.status).in_(
                    [DeploymentStatus.PENDING, DeploymentStatus.DISPATCHED]
                ),
            )
        ).first()
        if active is not None:
            session.rollback()
            raise HTTPException(
                409, "An artifact deployment is already active for this node target"
            )
        installed = session.exec(
            select(RuntimeNodeArtifact).where(
                RuntimeNodeArtifact.node_id == node.id,
                RuntimeNodeArtifact.logical_target == artifact.logical_target,
            )
        ).first()
        session.add(
            ArtifactDeployment(
                namespace_id=namespace_id,
                release_id=release.id,
                node_id=node.id,
                artifact_id=artifact.id,
                logical_target=artifact.logical_target,
                previous_artifact_id=installed.current_artifact_id
                if installed
                else None,
            )
        )
    session.commit()
    return _public_release(session, release)


@router.get("/releases/{release_id}", response_model=ReleasePublic)
def read_release(
    release_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> ReleasePublic:
    release = session.get(ArtifactRelease, release_id)
    if release is None or release.namespace_id != namespace_id:
        raise HTTPException(404, "Release not found")
    return _public_release(session, release)


@router.post(
    "/deployments/{deployment_id}/retry",
    response_model=DeploymentPublic,
    status_code=201,
)
def retry_deployment(
    deployment_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> DeploymentPublic:
    deployment = session.get(ArtifactDeployment, deployment_id)
    if deployment is None or deployment.namespace_id != namespace_id:
        raise HTTPException(404, "Deployment not found")
    if deployment.status not in {DeploymentStatus.FAILED, DeploymentStatus.EXPIRED}:
        raise HTTPException(409, "Only failed or expired deployments can be retried")
    session.exec(
        select(RuntimeNode)
        .where(RuntimeNode.id == deployment.node_id)
        .with_for_update()
    ).one()
    active = session.exec(
        select(ArtifactDeployment).where(
            ArtifactDeployment.node_id == deployment.node_id,
            ArtifactDeployment.logical_target == deployment.logical_target,
            col(ArtifactDeployment.status).in_(
                [DeploymentStatus.PENDING, DeploymentStatus.DISPATCHED]
            ),
        )
    ).first()
    if active is not None:
        raise HTTPException(409, "An artifact deployment is already active")
    retried = ArtifactReleaseService(session).retry(deployment)
    return DeploymentPublic(
        id=retried.id,
        node_id=retried.node_id,
        artifact_id=retried.artifact_id,
        previous_artifact_id=retried.previous_artifact_id,
        attempt=retried.attempt,
        status=retried.status,
        error=retried.error,
    )


@router.post(
    "/deployments/{deployment_id}/rollback",
    response_model=ReleasePublic,
    status_code=201,
)
def rollback_deployment(
    deployment_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> ReleasePublic:
    deployment = session.get(ArtifactDeployment, deployment_id)
    if (
        deployment is None
        or deployment.namespace_id != namespace_id
        or deployment.status != DeploymentStatus.APPLIED
        or deployment.previous_artifact_id is None
    ):
        raise HTTPException(409, "Deployment has no applied previous version")
    session.exec(
        select(RuntimeNode)
        .where(RuntimeNode.id == deployment.node_id)
        .with_for_update()
    ).one()
    current = session.exec(
        select(RuntimeNodeArtifact).where(
            RuntimeNodeArtifact.node_id == deployment.node_id,
            RuntimeNodeArtifact.logical_target == deployment.logical_target,
        )
    ).first()
    previous = session.get(RuntimeArtifact, deployment.previous_artifact_id)
    if (
        current is None
        or current.current_artifact_id != deployment.artifact_id
        or previous is None
        or previous.namespace_id != namespace_id
        or previous.logical_target != deployment.logical_target
    ):
        raise HTTPException(409, "Deployment is no longer the current target state")
    previously_applied = session.exec(
        select(ArtifactDeployment).where(
            ArtifactDeployment.node_id == deployment.node_id,
            ArtifactDeployment.artifact_id == previous.id,
            ArtifactDeployment.logical_target == deployment.logical_target,
            ArtifactDeployment.status == DeploymentStatus.APPLIED,
        )
    ).first()
    active = session.exec(
        select(ArtifactDeployment).where(
            ArtifactDeployment.node_id == deployment.node_id,
            ArtifactDeployment.logical_target == deployment.logical_target,
            col(ArtifactDeployment.status).in_(
                [DeploymentStatus.PENDING, DeploymentStatus.DISPATCHED]
            ),
        )
    ).first()
    if previously_applied is None or active is not None:
        raise HTTPException(409, "Rollback target is not safely installable")
    release = ArtifactRelease(
        namespace_id=namespace_id,
        artifact_id=deployment.previous_artifact_id,
        valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
        rollback_of_release_id=deployment.release_id,
        created_by=current_user.id,
    )
    session.add(release)
    session.flush()
    session.add(
        ArtifactDeployment(
            namespace_id=namespace_id,
            release_id=release.id,
            node_id=deployment.node_id,
            artifact_id=deployment.previous_artifact_id,
            logical_target=deployment.logical_target,
            previous_artifact_id=deployment.artifact_id,
        )
    )
    session.commit()
    return _public_release(session, release)


@node_router.get("/{deployment_id}/download", response_model=None)
def download_artifact(
    deployment_id: uuid.UUID, token: str, session: SessionDep
) -> StreamingResponse:
    try:
        claims = verify_artifact_download_token(token, deployment_id)
    except ArtifactDownloadTokenError as exc:
        raise HTTPException(403, str(exc)) from exc
    deployment = session.get(ArtifactDeployment, deployment_id)
    if (
        deployment is None
        or str(deployment.node_id) != claims.get("node_id")
        or str(deployment.artifact_id) != claims.get("artifact_id")
        or deployment.status
        not in {DeploymentStatus.PENDING, DeploymentStatus.DISPATCHED}
    ):
        raise HTTPException(404, "Artifact deployment not available")
    release = session.get(ArtifactRelease, deployment.release_id)
    artifact = session.get(RuntimeArtifact, deployment.artifact_id)
    if (
        release is None
        or artifact is None
        or release.valid_until <= datetime.now(timezone.utc)
        or artifact.storage_key != claims.get("storage_key")
    ):
        raise HTTPException(410, "Artifact release expired")
    source = artifact_storage().open(artifact.storage_key)

    def stream() -> Iterator[bytes]:
        try:
            while chunk := source.read(1024 * 1024):
                yield chunk
        finally:
            source.close()

    return StreamingResponse(stream(), media_type="application/zip")
