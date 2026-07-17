import difflib
import hashlib
import json
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
    ProjectSpecBinding,
    ProjectSpecLocation,
    RepositoryStatus,
    SpecBindingStatus,
    SpecLocationStatus,
    SpecScopeType,
    SpecStandard,
    SpecStandardVersion,
)
from app.runtime.models import (
    RuntimeInstance,
    RuntimeJob,
    RuntimeJobKind,
    RuntimeJobStatus,
    RuntimeProfile,
)


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


def validate_runtime_instance(
    session: Session, namespace_id: uuid.UUID, runtime_id: uuid.UUID | None
) -> RuntimeInstance | None:
    if runtime_id is None:
        return None
    runtime = session.get(RuntimeInstance, runtime_id)
    if (
        runtime is None
        or runtime.namespace_id != namespace_id
        or not runtime.enabled
    ):
        raise HTTPException(422, "Default Runtime must be available in the project namespace")
    return runtime


def get_repository(
    session: Session, repository_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectRepository:
    repository = session.get(ProjectRepository, repository_id)
    if repository is None or repository.project_id != project_id:
        raise HTTPException(404, "Project repository not found")
    return repository


def verified_repository_runtime_proof(
    session: Session,
    repository: ProjectRepository,
    runtime_id: uuid.UUID,
) -> dict[str, Any] | None:
    proof = repository.runtime_workspace_refs.get(str(runtime_id))
    if not isinstance(proof, dict) or not proof.get("runtime_job_id"):
        return None
    try:
        job_id = uuid.UUID(str(proof["runtime_job_id"]))
    except (TypeError, ValueError):
        return None
    job = session.get(RuntimeJob, job_id)
    if (
        job is None
        or job.kind != RuntimeJobKind.REPOSITORY_PROBE
        or job.status != RuntimeJobStatus.SUCCEEDED
        or (job.runtime_instance_id or job.runtime_profile_id) != runtime_id
        or not isinstance(job.result, dict)
        or job.result.get("commit") != repository.validated_commit
        or proof.get("commit") != repository.validated_commit
        or proof.get("workspace_ref") != job.result.get("workspace_ref")
        or proof.get("remote_url") != job.result.get("remote_url")
    ):
        return None
    return proof


def validate_spec_standard_manifest(manifest: dict[str, Any]) -> None:
    required_files = manifest.get("required_files", [])
    templates = manifest.get("templates", {})
    if not isinstance(required_files, list) or any(
        not isinstance(path, str) for path in required_files
    ):
        raise HTTPException(422, "Spec manifest required_files must be strings")
    if not isinstance(templates, dict) or any(
        not isinstance(path, str) or not isinstance(content, str)
        for path, content in templates.items()
    ):
        raise HTTPException(422, "Spec manifest templates must map paths to text")
    for path in [*required_files, *templates]:
        normalize_spec_path(path)


def canonical_spec_manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def build_spec_diff_preview(
    *,
    location: ProjectSpecLocation,
    repository: ProjectRepository,
    version: SpecStandardVersion,
    runtime_id: uuid.UUID,
    proof: dict[str, Any],
) -> dict[str, Any]:
    validate_spec_standard_manifest(version.manifest)
    proof_location = next(
        (
            item
            for item in proof.get("spec_locations", [])
            if str(item.get("spec_location_id")) == str(location.id)
        ),
        None,
    )
    if not isinstance(proof_location, dict):
        raise HTTPException(409, "Runtime proof has no content for this Spec location")
    actual_files = {
        str(item["path"]): item
        for item in proof_location.get("files", [])
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("content"), str)
    }
    templates = dict(version.manifest.get("templates", {}))
    required = set(version.manifest.get("required_files", []))
    target_paths = sorted(required | set(templates))
    files: list[dict[str, Any]] = []
    counts = {"add": 0, "modify": 0, "unchanged": 0, "missing_template": 0}
    for relative_path in target_paths:
        repository_path = (
            location.path
            if location.location_type.value == "file"
            else f"{location.path.rstrip('/')}/{relative_path}"
        )
        actual = actual_files.get(repository_path)
        expected_content = templates.get(relative_path)
        actual_content = str(actual["content"]) if actual else None
        if expected_content is None:
            action = "unchanged" if actual is not None else "missing_template"
        elif actual_content is None:
            action = "add"
        elif actual_content == expected_content:
            action = "unchanged"
        else:
            action = "modify"
        counts[action] += 1
        patch = ""
        if expected_content is not None and action in {"add", "modify"}:
            patch = "".join(
                difflib.unified_diff(
                    (actual_content or "").splitlines(keepends=True),
                    expected_content.splitlines(keepends=True),
                    fromfile=(
                        f"a/{repository_path}" if actual is not None else "/dev/null"
                    ),
                    tofile=f"b/{repository_path}",
                )
            )
        files.append(
            {
                "path": repository_path,
                "template_path": relative_path,
                "required": relative_path in required,
                "action": action,
                "actual_digest": actual.get("content_digest") if actual else None,
                "expected_digest": (
                    hashlib.sha256(expected_content.encode()).hexdigest()
                    if expected_content is not None
                    else None
                ),
                "patch": patch,
            }
        )
    return {
        "mode": "read_only_preview",
        "repository_id": str(repository.id),
        "runtime_id": str(runtime_id),
        "runtime_job_id": proof["runtime_job_id"],
        "validated_commit": repository.validated_commit,
        "path": location.path,
        "standard_version_id": str(version.id),
        "standard_content_digest": version.content_digest,
        "summary": counts,
        "files": files,
        "has_conflicts": counts["missing_template"] > 0,
        "message": "No Git content was modified; apply changes through an explicit Workflow node",
    }


def reconcile_repository_validation(
    session: Session, repository: ProjectRepository
) -> bool:
    if repository.validation_job_id is None:
        return False
    job = session.get(RuntimeJob, repository.validation_job_id)
    if job is None:
        repository.status = RepositoryStatus.UNAVAILABLE
        repository.validation_error = {"code": "repository_validation_job_missing"}
        session.add(repository)
        return True
    if job.status in {
        RuntimeJobStatus.QUEUED,
        RuntimeJobStatus.DISPATCHED,
        RuntimeJobStatus.RUNNING,
    }:
        return False
    locations = session.exec(
        select(ProjectSpecLocation).where(
            ProjectSpecLocation.repository_id == repository.id
        )
    ).all()
    if job.status != RuntimeJobStatus.SUCCEEDED or not isinstance(job.result, dict):
        repository.status = RepositoryStatus.UNAVAILABLE
        repository.validation_error = job.error or {
            "code": "repository_validation_failed"
        }
        for location in locations:
            location.status = SpecLocationStatus.UNAVAILABLE
            location.updated_at = utcnow()
            session.add(location)
        session.add(repository)
        return True
    result = job.result
    runtime_id = str(job.runtime_instance_id or job.runtime_profile_id)
    repository.runtime_workspace_refs = {
        **repository.runtime_workspace_refs,
        runtime_id: {
            "workspace_ref": result["workspace_ref"],
            "commit": result["commit"],
            "remote_url": result["remote_url"],
            "spec_locations": result.get("spec_locations", []),
            "verified_at": (job.completed_at or utcnow()).isoformat(),
            "runtime_job_id": str(job.id),
        },
    }
    repository.validated_commit = str(result["commit"])
    repository.status = RepositoryStatus.AVAILABLE
    repository.validation_error = None
    repository.updated_at = utcnow()
    spec_by_id = {
        str(item["spec_location_id"]): item for item in result.get("spec_locations", [])
    }
    for location in locations:
        location.status = (
            SpecLocationStatus.VALID
            if str(location.id) in spec_by_id
            else SpecLocationStatus.UNAVAILABLE
        )
        location.updated_at = utcnow()
        session.add(location)
        binding = session.exec(
            select(ProjectSpecBinding).where(
                ProjectSpecBinding.spec_location_id == location.id
            )
        ).first()
        if binding is not None:
            binding.status = (
                SpecBindingStatus.VALID
                if location.status == SpecLocationStatus.VALID
                else SpecBindingStatus.PENDING
            )
            binding.validated_commit = (
                repository.validated_commit
                if binding.status == SpecBindingStatus.VALID
                else None
            )
            binding.updated_at = utcnow()
            session.add(binding)
    session.add(repository)
    return True


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
        "default_runtime_instance_id": project.default_runtime_instance_id,
        "member_ids": member_ids,
        "created_by": project.created_by,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "archived_at": project.archived_at,
    }
