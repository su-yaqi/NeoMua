import posixpath
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app import crud
from app.models import NamespaceRole, User, UserNamespaceLink
from app.project_management.models import (
    Project,
    ProjectMember,
    ProjectRepository,
    ProjectSpecLocation,
    SpecScopeType,
    SpecStandard,
    SpecStandardVersion,
)
from app.runtime.models import RuntimeProfile


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_spec_path(value: str) -> str:
    """Return a canonical repository-relative POSIX path without escape forms."""
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise HTTPException(
            422, "Spec path must be a normalized repository-relative path"
        )
    normalized = posixpath.normpath(raw)
    if normalized in {"", "."} or normalized.startswith("../"):
        raise HTTPException(422, "Spec path escapes the repository")
    return normalized.rstrip("/")


def get_project(
    session: Session, project_id: uuid.UUID, namespace_id: uuid.UUID
) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.namespace_id != namespace_id:
        raise HTTPException(404, "Project not found")
    return project


def require_project_member(
    session: Session,
    project_id: uuid.UUID,
    namespace_id: uuid.UUID,
    user: User,
) -> Project:
    project = get_project(session, project_id, namespace_id)
    if user.is_superuser:
        return project
    membership = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    ).first()
    if membership is None:
        raise HTTPException(403, "Project membership required")
    return project


def require_active_project(project: Project) -> None:
    if project.status.value == "archived":
        raise HTTPException(409, "Project is archived and read-only")


def validate_member_ids(
    session: Session, namespace_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    unique_ids = list(dict.fromkeys(user_ids))
    if not unique_ids:
        return []
    rows = session.exec(
        select(UserNamespaceLink).where(
            UserNamespaceLink.namespace_id == namespace_id,
            col(UserNamespaceLink.user_id).in_(unique_ids),
        )
    ).all()
    found = {row.user_id for row in rows}
    missing = [str(user_id) for user_id in unique_ids if user_id not in found]
    if missing:
        raise HTTPException(422, {"users_not_in_namespace": missing})
    return unique_ids


def validate_runtime(
    session: Session, namespace_id: uuid.UUID, runtime_id: uuid.UUID | None
) -> RuntimeProfile | None:
    if runtime_id is None:
        return None
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(422, "Default Runtime must belong to the project namespace")
    return runtime


def get_repository(
    session: Session, repository_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectRepository:
    repository = session.get(ProjectRepository, repository_id)
    if repository is None or repository.project_id != project_id:
        raise HTTPException(404, "Project repository not found")
    return repository


def get_spec_location(
    session: Session, location_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectSpecLocation:
    location = session.get(ProjectSpecLocation, location_id)
    if location is None or location.project_id != project_id:
        raise HTTPException(404, "Project Spec location not found")
    return location


def can_manage_namespace(session: Session, namespace_id: uuid.UUID, user: User) -> bool:
    if user.is_superuser:
        return True
    role = crud.get_namespace_role(
        session=session, user_id=user.id, namespace_id=namespace_id
    )
    return role in {NamespaceRole.ADMIN, NamespaceRole.DEVELOPER}


def require_standard_visible(
    session: Session, standard_id: uuid.UUID, namespace_id: uuid.UUID
) -> SpecStandard:
    standard = session.get(SpecStandard, standard_id)
    if standard is None:
        raise HTTPException(404, "Spec standard not found")
    if (
        standard.scope_type == SpecScopeType.NAMESPACE
        and standard.namespace_id != namespace_id
    ):
        raise HTTPException(404, "Spec standard not found")
    return standard


def require_version_visible(
    session: Session, version_id: uuid.UUID, namespace_id: uuid.UUID
) -> tuple[SpecStandardVersion, SpecStandard]:
    version = session.get(SpecStandardVersion, version_id)
    if version is None:
        raise HTTPException(404, "Spec standard version not found")
    standard = require_standard_visible(session, version.standard_id, namespace_id)
    return version, standard


def project_public(session: Session, project: Project) -> dict[str, Any]:
    member_ids = session.exec(
        select(ProjectMember.user_id).where(ProjectMember.project_id == project.id)
    ).all()
    return {
        "id": project.id,
        "namespace_id": project.namespace_id,
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "default_runtime_id": project.default_runtime_id,
        "member_ids": member_ids,
        "created_by": project.created_by,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "archived_at": project.archived_at,
    }
