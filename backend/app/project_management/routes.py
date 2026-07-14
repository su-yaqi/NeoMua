import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, or_, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_manager,
    require_namespace_member,
)
from app.project_management.models import (
    Project,
    ProjectMember,
    ProjectRepository,
    ProjectSpecBinding,
    ProjectSpecLocation,
    ProjectStatus,
    RepositoryStatus,
    SpecBindingStatus,
    SpecLocationStatus,
    SpecScopeType,
    SpecStandard,
    SpecStandardVersion,
    SpecVersionStatus,
)
from app.project_management.schemas import (
    ProjectCreate,
    ProjectMembersUpdate,
    ProjectUpdate,
    RepositoryCreate,
    RepositoryUpdate,
    RepositoryValidationRequest,
    SpecBindingUpdate,
    SpecLocationCreate,
    SpecLocationUpdate,
    SpecStandardCompleteCreate,
    SpecStandardCreate,
    SpecStandardVersionCreate,
    SpecVersionDeprecate,
)
from app.project_management.service import (
    build_spec_diff_preview,
    can_manage_namespace,
    canonical_spec_manifest_digest,
    get_project,
    get_repository,
    get_spec_location,
    normalize_spec_path,
    project_public,
    reconcile_repository_validation,
    require_active_project,
    require_project_member,
    require_standard_visible,
    require_version_visible,
    utcnow,
    validate_member_ids,
    validate_runtime,
    validate_spec_standard_manifest,
    verified_repository_runtime_proof,
)
from app.runtime.jobs import enqueue_runtime_job
from app.runtime.models import RuntimeJob, RuntimeJobKind, RuntimeProfile

router = APIRouter(tags=["projects"])


def _integrity_error(_exc: IntegrityError, detail: str) -> HTTPException:
    return HTTPException(409, detail)


def _manager_project(
    session: SessionDep, project_id: uuid.UUID, namespace_id: uuid.UUID
) -> Project:
    project = get_project(session, project_id, namespace_id)
    require_active_project(project)
    return project


@router.get("/projects")
def list_projects(
    session: SessionDep,
    current_user: CurrentUser,
    include_archived: bool = False,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    statement = select(Project).where(Project.namespace_id == namespace_id)
    if not include_archived:
        statement = statement.where(Project.status == ProjectStatus.ACTIVE)
    if not can_manage_namespace(session, namespace_id, current_user):
        statement = statement.join(ProjectMember).where(
            ProjectMember.user_id == current_user.id
        )
    rows = session.exec(statement.order_by(col(Project.updated_at).desc())).all()
    return {"data": [project_public(session, row) for row in rows], "count": len(rows)}


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> dict[str, Any]:
    validate_runtime(session, namespace_id, body.default_runtime_id)
    requested_member_ids = list(body.member_ids)
    if not current_user.is_superuser:
        requested_member_ids.insert(0, current_user.id)
    member_ids = validate_member_ids(session, namespace_id, requested_member_ids)
    project = Project(
        namespace_id=namespace_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        default_runtime_id=body.default_runtime_id,
        created_by=current_user.id,
    )
    session.add(project)
    try:
        session.flush()
        session.add_all(
            [
                ProjectMember(project_id=project.id, user_id=user_id)
                for user_id in member_ids
            ]
        )
        session.add_all(
            [
                ProjectRepository(project_id=project.id, **repository.model_dump())
                for repository in body.initial_repositories
            ]
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _integrity_error(exc, "Project slug already exists in this namespace")
    session.refresh(project)
    return project_public(session, project)


@router.get("/projects/{project_id}")
def read_project(
    project_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    project = require_project_member(session, project_id, namespace_id, current_user)
    return project_public(session, project)


@router.patch("/projects/{project_id}")
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> dict[str, Any]:
    project = _manager_project(session, project_id, namespace_id)
    update = body.model_dump(exclude_unset=True)
    archive = update.pop("archive", None)
    if "default_runtime_id" in update:
        validate_runtime(session, namespace_id, update["default_runtime_id"])
    for key, value in update.items():
        setattr(project, key, value)
    if archive:
        project.status = ProjectStatus.ARCHIVED
        project.archived_at = utcnow()
    project.updated_at = utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return project_public(session, project)


@router.get("/projects/{project_id}/members")
def list_project_members(
    project_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    require_project_member(session, project_id, namespace_id, current_user)
    rows = session.exec(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    ).all()
    return {"data": rows, "count": len(rows)}


@router.put("/projects/{project_id}/members")
def replace_project_members(
    project_id: uuid.UUID,
    body: ProjectMembersUpdate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> dict[str, Any]:
    project = _manager_project(session, project_id, namespace_id)
    user_ids = validate_member_ids(session, namespace_id, body.user_ids)
    session.exec(
        delete(ProjectMember).where(col(ProjectMember.project_id) == project.id)
    )
    session.add_all(
        [ProjectMember(project_id=project.id, user_id=user_id) for user_id in user_ids]
    )
    session.commit()
    return {"data": user_ids, "count": len(user_ids)}


@router.get("/projects/{project_id}/repositories")
def list_repositories(
    project_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    require_project_member(session, project_id, namespace_id, current_user)
    rows = session.exec(
        select(ProjectRepository).where(ProjectRepository.project_id == project_id)
    ).all()
    changed = False
    for row in rows:
        changed = reconcile_repository_validation(session, row) or changed
    if changed:
        session.commit()
        for row in rows:
            session.refresh(row)
    return {"data": rows, "count": len(rows)}


@router.post("/projects/{project_id}/repositories", status_code=201)
def create_repository(
    project_id: uuid.UUID,
    body: RepositoryCreate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> ProjectRepository:
    _manager_project(session, project_id, namespace_id)
    repository = ProjectRepository(project_id=project_id, **body.model_dump())
    session.add(repository)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _integrity_error(exc, "Repository is already bound to this project")
    session.refresh(repository)
    return repository


@router.patch("/projects/{project_id}/repositories/{repository_id}")
def update_repository(
    project_id: uuid.UUID,
    repository_id: uuid.UUID,
    body: RepositoryUpdate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> ProjectRepository:
    _manager_project(session, project_id, namespace_id)
    repository = get_repository(session, repository_id, project_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(repository, key, value)
    repository.status = RepositoryStatus.UNVALIDATED
    repository.validation_error = None
    repository.updated_at = utcnow()
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return repository


@router.delete("/projects/{project_id}/repositories/{repository_id}", status_code=204)
def delete_repository(
    project_id: uuid.UUID,
    repository_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> Response:
    _manager_project(session, project_id, namespace_id)
    repository = get_repository(session, repository_id, project_id)
    session.delete(repository)
    session.commit()
    return Response(status_code=204)


@router.post(
    "/projects/{project_id}/repositories/{repository_id}/validate", status_code=202
)
def validate_repository_access(
    project_id: uuid.UUID,
    repository_id: uuid.UUID,
    body: RepositoryValidationRequest,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> ProjectRepository:
    _manager_project(session, project_id, namespace_id)
    repository = get_repository(session, repository_id, project_id)
    if reconcile_repository_validation(session, repository):
        session.commit()
    runtime = validate_runtime(session, namespace_id, body.runtime_id)
    assert isinstance(runtime, RuntimeProfile)
    workspaces = runtime.config.get("repository_workspaces", {})
    proof = workspaces.get(repository.remote_url)
    if (
        not isinstance(proof, dict)
        or not proof.get("workspace_ref")
        or not (proof.get("workspace_path") or proof.get("path"))
    ):
        repository.status = RepositoryStatus.UNAVAILABLE
        repository.validation_error = {
            "code": "runtime_repository_proof_missing",
            "runtime_id": str(runtime.id),
            "message": "Runtime has no configured workspace path for this repository",
        }
        session.add(repository)
        session.commit()
        session.refresh(repository)
        return repository
    locations = session.exec(
        select(ProjectSpecLocation).where(
            ProjectSpecLocation.repository_id == repository.id
        )
    ).all()
    payload = {
        "repository_id": str(repository.id),
        "remote_url": repository.remote_url,
        "default_branch": repository.default_branch,
        "workspace_ref": str(proof["workspace_ref"]),
        "workspace_path": str(proof.get("workspace_path") or proof.get("path")),
        "allowed_roots": runtime.config.get("allowed_working_roots", []),
        "spec_locations": [
            {
                "id": str(location.id),
                "path": location.path,
                "location_type": location.location_type.value,
            }
            for location in locations
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    job = enqueue_runtime_job(
        session,
        namespace_id=namespace_id,
        runtime_id=runtime.id,
        kind=RuntimeJobKind.REPOSITORY_PROBE,
        payload=payload,
        idempotency_key=f"repository:{repository.id}:validation:{fingerprint}",
    )
    repository.validation_job_id = job.id
    repository.status = RepositoryStatus.UNVALIDATED
    repository.validation_error = {
        "code": "runtime_repository_validation_pending",
        "runtime_id": str(runtime.id),
        "runtime_job_id": str(job.id),
    }
    repository.validated_commit = None
    repository.updated_at = utcnow()
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return repository


@router.get("/projects/{project_id}/spec-locations")
def list_spec_locations(
    project_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    require_project_member(session, project_id, namespace_id, current_user)
    rows = session.exec(
        select(ProjectSpecLocation).where(ProjectSpecLocation.project_id == project_id)
    ).all()
    bindings = session.exec(
        select(ProjectSpecBinding).where(ProjectSpecBinding.project_id == project_id)
    ).all()
    binding_by_location = {row.spec_location_id: row for row in bindings}
    return {
        "data": [
            {
                **row.model_dump(),
                "binding": binding_by_location.get(row.id),
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.post("/projects/{project_id}/spec-locations", status_code=201)
def create_spec_location(
    project_id: uuid.UUID,
    body: SpecLocationCreate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> ProjectSpecLocation:
    _manager_project(session, project_id, namespace_id)
    get_repository(session, body.repository_id, project_id)
    location = ProjectSpecLocation(
        project_id=project_id,
        repository_id=body.repository_id,
        path=normalize_spec_path(body.path),
        location_type=body.location_type,
        description=body.description,
    )
    session.add(location)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _integrity_error(exc, "Spec path already exists for this repository")
    session.refresh(location)
    return location


@router.patch("/projects/{project_id}/spec-locations/{location_id}")
def update_spec_location(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    body: SpecLocationUpdate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> ProjectSpecLocation:
    _manager_project(session, project_id, namespace_id)
    location = get_spec_location(session, location_id, project_id)
    update = body.model_dump(exclude_unset=True)
    if "path" in update:
        update["path"] = normalize_spec_path(update["path"])
    for key, value in update.items():
        setattr(location, key, value)
    location.status = SpecLocationStatus.PENDING_INITIALIZATION
    location.updated_at = utcnow()
    session.add(location)
    session.commit()
    session.refresh(location)
    return location


@router.delete("/projects/{project_id}/spec-locations/{location_id}", status_code=204)
def delete_spec_location(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> Response:
    _manager_project(session, project_id, namespace_id)
    location = get_spec_location(session, location_id, project_id)
    session.delete(location)
    session.commit()
    return Response(status_code=204)


@router.get("/spec-standards")
def list_spec_standards(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    rows = session.exec(
        select(SpecStandard).where(
            or_(
                SpecStandard.scope_type == SpecScopeType.PLATFORM,
                SpecStandard.namespace_id == namespace_id,
            )
        )
    ).all()
    return {"data": rows, "count": len(rows)}


@router.post("/spec-standards", status_code=201)
def create_spec_standard(
    body: SpecStandardCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> SpecStandard:
    if body.scope_type == SpecScopeType.PLATFORM:
        if not current_user.is_superuser:
            raise HTTPException(403, "Platform Spec standards require a superuser")
        standard_namespace_id = None
    else:
        if not can_manage_namespace(session, namespace_id, current_user):
            raise HTTPException(403, "Namespace admin or developer privilege required")
        standard_namespace_id = namespace_id
    standard = SpecStandard(
        scope_type=body.scope_type,
        namespace_id=standard_namespace_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        created_by=current_user.id,
    )
    session.add(standard)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _integrity_error(exc, "Spec standard slug already exists in this scope")
    session.refresh(standard)
    return standard


@router.post("/spec-standards/complete", status_code=201)
def create_spec_standard_complete(
    body: SpecStandardCompleteCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    if body.scope_type == SpecScopeType.PLATFORM:
        if not current_user.is_superuser:
            raise HTTPException(403, "Platform Spec standards require a superuser")
        standard_namespace_id = None
    else:
        if not can_manage_namespace(session, namespace_id, current_user):
            raise HTTPException(
                403, "Namespace admin or developer privilege required"
            )
        standard_namespace_id = namespace_id
    validate_spec_standard_manifest(body.manifest)
    content_digest = canonical_spec_manifest_digest(body.manifest)
    standard = SpecStandard(
        scope_type=body.scope_type,
        namespace_id=standard_namespace_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        created_by=current_user.id,
    )
    version = SpecStandardVersion(
        standard_id=standard.id,
        version=body.version,
        manifest=body.manifest,
        content_digest=content_digest,
        storage_ref=body.storage_ref,
        created_by=current_user.id,
    )
    session.add(standard)
    session.add(version)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _integrity_error(
            exc, "Spec standard identifier, version, or digest already exists"
        )
    session.refresh(standard)
    session.refresh(version)
    return {"standard": standard, "version": version}


@router.get("/spec-standards/{standard_id}/versions")
def list_standard_versions(
    standard_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    require_standard_visible(session, standard_id, namespace_id)
    rows = session.exec(
        select(SpecStandardVersion)
        .where(SpecStandardVersion.standard_id == standard_id)
        .order_by(col(SpecStandardVersion.created_at).desc())
    ).all()
    return {"data": rows, "count": len(rows)}


@router.post("/spec-standards/{standard_id}/versions", status_code=201)
def publish_standard_version(
    standard_id: uuid.UUID,
    body: SpecStandardVersionCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> SpecStandardVersion:
    standard = require_standard_visible(session, standard_id, namespace_id)
    if standard.scope_type == SpecScopeType.PLATFORM and not current_user.is_superuser:
        raise HTTPException(403, "Platform Spec standards require a superuser")
    if standard.scope_type == SpecScopeType.NAMESPACE and not can_manage_namespace(
        session, namespace_id, current_user
    ):
        raise HTTPException(403, "Namespace admin or developer privilege required")
    validate_spec_standard_manifest(body.manifest)
    computed_digest = canonical_spec_manifest_digest(body.manifest)
    if computed_digest != body.content_digest:
        raise HTTPException(
            409,
            {
                "code": "spec_manifest_digest_mismatch",
                "expected": computed_digest,
                "received": body.content_digest,
            },
        )
    version = SpecStandardVersion(
        standard_id=standard.id, created_by=current_user.id, **body.model_dump()
    )
    session.add(version)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _integrity_error(exc, "Spec standard version or digest already exists")
    session.refresh(version)
    return version


@router.patch("/spec-standard-versions/{version_id}")
def deprecate_standard_version(
    version_id: uuid.UUID,
    body: SpecVersionDeprecate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> SpecStandardVersion:
    version, standard = require_version_visible(session, version_id, namespace_id)
    if standard.scope_type == SpecScopeType.PLATFORM and not current_user.is_superuser:
        raise HTTPException(403, "Platform Spec standards require a superuser")
    if standard.scope_type == SpecScopeType.NAMESPACE and not can_manage_namespace(
        session, namespace_id, current_user
    ):
        raise HTTPException(403, "Namespace admin or developer privilege required")
    version.status = (
        SpecVersionStatus.DEPRECATED if body.deprecated else SpecVersionStatus.ACTIVE
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


@router.put("/projects/{project_id}/spec-locations/{location_id}/binding")
def bind_spec_standard(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    body: SpecBindingUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> ProjectSpecBinding:
    _manager_project(session, project_id, namespace_id)
    location = get_spec_location(session, location_id, project_id)
    require_version_visible(session, body.standard_version_id, namespace_id)
    binding = session.exec(
        select(ProjectSpecBinding).where(
            ProjectSpecBinding.spec_location_id == location.id
        )
    ).first()
    if binding is None:
        binding = ProjectSpecBinding(
            project_id=project_id,
            spec_location_id=location.id,
            standard_version_id=body.standard_version_id,
            updated_by=current_user.id,
        )
    else:
        binding.standard_version_id = body.standard_version_id
        binding.status = SpecBindingStatus.PENDING
        binding.validated_commit = None
        binding.diff_preview = None
        binding.updated_by = current_user.id
        binding.updated_at = utcnow()
    session.add(binding)
    session.commit()
    session.refresh(binding)
    return binding


@router.post("/projects/{project_id}/spec-locations/{location_id}/diff")
def preview_spec_diff(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_manager),
) -> dict[str, Any]:
    _manager_project(session, project_id, namespace_id)
    location = get_spec_location(session, location_id, project_id)
    repository = get_repository(session, location.repository_id, project_id)
    binding = session.exec(
        select(ProjectSpecBinding).where(
            ProjectSpecBinding.spec_location_id == location.id
        )
    ).first()
    if binding is None:
        raise HTTPException(409, "Spec location has no standard version binding")
    version, _standard = require_version_visible(
        session, binding.standard_version_id, namespace_id
    )
    if (
        repository.status != RepositoryStatus.AVAILABLE
        or not repository.validated_commit
    ):
        raise HTTPException(
            409, "Repository must have current Runtime validation proof"
        )
    if repository.validation_job_id is None:
        raise HTTPException(409, "Repository has no current Runtime validation job")
    validation_job = session.get(RuntimeJob, repository.validation_job_id)
    if validation_job is None:
        raise HTTPException(409, "Repository Runtime validation job is unavailable")
    proof = verified_repository_runtime_proof(
        session, repository, validation_job.runtime_profile_id
    )
    if proof is None:
        raise HTTPException(409, "Repository Runtime validation proof is unavailable")
    preview = build_spec_diff_preview(
        location=location,
        repository=repository,
        version=version,
        runtime_id=validation_job.runtime_profile_id,
        proof=proof,
    )
    binding.diff_preview = preview
    binding.updated_at = datetime.now(timezone.utc)
    session.add(binding)
    session.commit()
    return preview
