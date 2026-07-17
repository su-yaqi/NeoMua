"""Skill bundle signing, subscription, and runtime reconciliation helpers."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.agent_management.capabilities import canonical_bytes, canonical_digest
from app.agent_management.capability_models import (
    AgentActivation,
    AgentDeployment,
    AgentDeploymentStatus,
    AgentRelease,
    RuntimeAgentRelease,
    SkillDefinition,
    SkillVersion,
)
from app.core.config import settings
from app.runtime.artifacts.local_storage import LocalArtifactStorage
from app.runtime.artifacts.s3_storage import S3ArtifactStorage
from app.runtime.artifacts.signing import configured_artifact_signer
from app.runtime.artifacts.storage import ArtifactStorage
from app.runtime.models import RuntimeSkillState


def skill_storage() -> ArtifactStorage:
    if settings.ARTIFACT_STORAGE_BACKEND == "local":
        return LocalArtifactStorage(Path(settings.ARTIFACT_LOCAL_ROOT))
    if settings.ARTIFACT_STORAGE_BACKEND == "s3" and settings.ARTIFACT_S3_BUCKET:
        return S3ArtifactStorage(
            bucket=settings.ARTIFACT_S3_BUCKET,
            endpoint_url=settings.ARTIFACT_S3_ENDPOINT,
            access_key=settings.ARTIFACT_S3_ACCESS_KEY,
            secret_key=settings.ARTIFACT_S3_SECRET_KEY,
            region=settings.ARTIFACT_S3_REGION,
        )
    raise RuntimeError("Configured immutable Skill storage is unavailable")


def signed_skill_manifest(
    skill: SkillDefinition, version: SkillVersion
) -> dict[str, Any]:
    return {
        "schema_version": "skill-bundle-v1",
        "namespace_id": str(skill.namespace_id),
        "skill_id": str(skill.id),
        "skill_slug": skill.slug,
        "version_id": str(version.id),
        "version": version.version,
        "content_sha256": version.content_sha256,
        "size": version.size,
        "files": version.manifest.get("files", []),
    }


def ensure_skill_version_signature(
    skill: SkillDefinition, version: SkillVersion
) -> None:
    manifest = signed_skill_manifest(skill, version)
    manifest_digest = canonical_digest(manifest)
    if (
        version.manifest_digest == manifest_digest
        and version.signature
        and version.signing_public_key
    ):
        return
    signer = configured_artifact_signer()
    version.manifest_digest = manifest_digest
    version.signature = signer.sign(canonical_bytes(manifest))
    version.signing_public_key = signer.public_key()


def mark_skill_current_changed(session: Session, skill: SkillDefinition) -> None:
    """Advance desired state without touching the last successfully applied state."""
    if skill.current_version_id is None:
        return
    version = session.get(SkillVersion, skill.current_version_id)
    if version is None or version.skill_id != skill.id:
        raise ValueError("Skill current version is invalid")
    ensure_skill_version_signature(skill, version)
    rows = session.exec(
        select(RuntimeSkillState).where(RuntimeSkillState.skill_id == skill.id)
    ).all()
    now = datetime.now(timezone.utc)
    for row in rows:
        if row.subscription_count <= 0:
            continue
        if (
            row.desired_version_id == version.id
            and row.desired_digest == version.content_sha256
        ):
            continue
        row.desired_version_id = version.id
        row.desired_digest = version.content_sha256
        row.generation += 1
        row.status = "pending"
        row.retry_count = 0
        row.next_retry_at = None
        row.last_error = None
        row.updated_at = now
        session.add(row)
    session.add(version)


def ensure_release_skill_subscriptions(
    session: Session,
    release: AgentRelease,
    runtime_profile_id: uuid.UUID,
) -> list[RuntimeSkillState]:
    if release.resolved_spec_schema_version != "1.1":
        return []
    states: list[RuntimeSkillState] = []
    for item in release.resolved_spec.get("skills", []):
        try:
            skill_id = uuid.UUID(str(item["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Agent Release contains an invalid Skill identity"
            ) from exc
        skill = session.get(SkillDefinition, skill_id)
        version = (
            session.get(SkillVersion, skill.current_version_id)
            if skill and skill.current_version_id
            else None
        )
        if (
            skill is None
            or skill.namespace_id != release.namespace_id
            or version is None
            or version.skill_id != skill.id
            or version.deprecated
        ):
            raise ValueError(
                f"Skill {item.get('slug', skill_id)} has no usable current version"
            )
        ensure_skill_version_signature(skill, version)
        state = session.exec(
            select(RuntimeSkillState).where(
                RuntimeSkillState.runtime_profile_id == runtime_profile_id,
                RuntimeSkillState.skill_id == skill.id,
            )
        ).first()
        if state is None:
            state = RuntimeSkillState(
                namespace_id=release.namespace_id,
                runtime_profile_id=runtime_profile_id,
                skill_id=skill.id,
                desired_version_id=version.id,
                desired_digest=version.content_sha256,
                subscription_count=0,
            )
        else:
            if (
                state.desired_version_id != version.id
                or state.desired_digest != version.content_sha256
            ):
                state.desired_version_id = version.id
                state.desired_digest = version.content_sha256
                state.generation += 1
                state.status = "pending"
                state.retry_count = 0
                state.next_retry_at = None
                state.last_error = None
            state.updated_at = datetime.now(timezone.utc)
        state.subscription_count = len(
            _runtime_skill_reference_releases(
                session,
                runtime_profile_id,
                skill.id,
                include_release=release,
            )
        )
        session.add(version)
        session.add(state)
        states.append(state)
    return states


def _runtime_skill_reference_releases(
    session: Session,
    runtime_profile_id: uuid.UUID,
    skill_id: uuid.UUID,
    *,
    include_release: AgentRelease | None = None,
) -> set[uuid.UUID]:
    releases: dict[uuid.UUID, AgentRelease] = {}
    if include_release is not None:
        releases[include_release.id] = include_release
    for binding in session.exec(
        select(RuntimeAgentRelease).where(
            RuntimeAgentRelease.runtime_profile_id == runtime_profile_id
        )
    ).all():
        release = session.get(AgentRelease, binding.current_release_id)
        if release is not None:
            releases[release.id] = release
    for deployment in session.exec(
        select(AgentDeployment).where(
            AgentDeployment.runtime_profile_id == runtime_profile_id,
            AgentDeployment.status.in_(
                [AgentDeploymentStatus.PENDING, AgentDeploymentStatus.DISPATCHED]
            ),
        )
    ).all():
        activation = session.get(AgentActivation, deployment.activation_id)
        release = (
            session.get(AgentRelease, activation.release_id) if activation else None
        )
        if release is not None:
            releases[release.id] = release
    return {
        release.id
        for release in releases.values()
        if release.resolved_spec_schema_version == "1.1"
        and any(
            str(item.get("id")) == str(skill_id)
            for item in release.resolved_spec.get("skills", [])
        )
    }


def reconcile_runtime_skill_subscriptions(
    session: Session, runtime_profile_id: uuid.UUID
) -> None:
    rows = session.exec(
        select(RuntimeSkillState).where(
            RuntimeSkillState.runtime_profile_id == runtime_profile_id
        )
    ).all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.subscription_count = len(
            _runtime_skill_reference_releases(session, runtime_profile_id, row.skill_id)
        )
        if row.subscription_count == 0:
            row.status = "orphaned"
        row.updated_at = now
        session.add(row)


def release_skills_ready(
    session: Session, release: AgentRelease, runtime_profile_id: uuid.UUID
) -> bool:
    if release.resolved_spec_schema_version != "1.1":
        return True
    for item in release.resolved_spec.get("skills", []):
        try:
            skill_id = uuid.UUID(str(item["id"]))
        except (KeyError, TypeError, ValueError):
            return False
        state = session.exec(
            select(RuntimeSkillState).where(
                RuntimeSkillState.runtime_profile_id == runtime_profile_id,
                RuntimeSkillState.skill_id == skill_id,
            )
        ).first()
        if (
            state is None
            or state.status != "applied"
            or state.applied_generation != state.generation
            or state.applied_version_id != state.desired_version_id
            or state.applied_digest != state.desired_digest
        ):
            return False
    return True


def release_skill_blockers(
    session: Session, release: AgentRelease, runtime_profile_id: uuid.UUID
) -> list[dict[str, Any]]:
    if release.resolved_spec_schema_version != "1.1":
        return []
    blockers: list[dict[str, Any]] = []
    for item in release.resolved_spec.get("skills", []):
        try:
            skill_id = uuid.UUID(str(item["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        state = session.exec(
            select(RuntimeSkillState).where(
                RuntimeSkillState.runtime_profile_id == runtime_profile_id,
                RuntimeSkillState.skill_id == skill_id,
                or_(
                    RuntimeSkillState.status == "blocked",
                    (
                        (RuntimeSkillState.status == "failed")
                        & (RuntimeSkillState.retry_count >= 5)
                    ),
                ),
            )
        ).first()
        if state is not None:
            blockers.append(
                {
                    "skill_id": str(skill_id),
                    "skill_slug": item.get("slug"),
                    "status": state.status,
                    "error": state.last_error,
                }
            )
    return blockers


def release_skills_committing(
    session: Session, release: AgentRelease, runtime_profile_id: uuid.UUID
) -> bool:
    if release.resolved_spec_schema_version != "1.1":
        return False
    skill_ids: list[uuid.UUID] = []
    for item in release.resolved_spec.get("skills", []):
        try:
            skill_ids.append(uuid.UUID(str(item["id"])))
        except (KeyError, TypeError, ValueError):
            return True
    if not skill_ids:
        return False
    return (
        session.exec(
            select(RuntimeSkillState.id).where(
                RuntimeSkillState.runtime_profile_id == runtime_profile_id,
                col(RuntimeSkillState.skill_id).in_(skill_ids),
                RuntimeSkillState.status == "committing",
            )
        ).first()
        is not None
    )


class SkillDownloadTokenError(ValueError):
    pass


def issue_skill_download_token(
    *,
    attempt_id: uuid.UUID,
    node_id: uuid.UUID,
    runtime_profile_id: uuid.UUID,
    skill_id: uuid.UUID,
    version_id: uuid.UUID,
    content_sha256: str,
    storage_key: str,
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "aud": "neomua-skill-download",
            "attempt_id": str(attempt_id),
            "node_id": str(node_id),
            "runtime_profile_id": str(runtime_profile_id),
            "skill_id": str(skill_id),
            "version_id": str(version_id),
            "content_sha256": content_sha256,
            "storage_key": storage_key,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def verify_skill_download_token(token: str, attempt_id: uuid.UUID) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            audience="neomua-skill-download",
        )
    except jwt.PyJWTError as exc:
        raise SkillDownloadTokenError(
            "invalid or expired Skill download token"
        ) from exc
    if claims.get("attempt_id") != str(attempt_id):
        raise SkillDownloadTokenError("Skill download token scope mismatch")
    return claims


def next_pending_skill_state(
    session: Session, *, runtime_profile_id: uuid.UUID | None = None
) -> RuntimeSkillState | None:
    query = select(RuntimeSkillState).where(
        RuntimeSkillState.subscription_count > 0,
        RuntimeSkillState.status.in_(["pending", "failed"]),
        col(RuntimeSkillState.desired_version_id).is_not(None),
    )
    if runtime_profile_id is not None:
        query = query.where(RuntimeSkillState.runtime_profile_id == runtime_profile_id)
    return session.exec(
        query.order_by(col(RuntimeSkillState.updated_at)).with_for_update(
            skip_locked=True
        )
    ).first()
