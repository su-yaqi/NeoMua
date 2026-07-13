import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import Namespace, NamespaceCreate, NamespaceRole
from app.project_management.models import (
    ProjectRepository,
    ProjectSpecLocation,
    SpecLocationType,
    SpecStandardVersion,
)
from app.project_management.service import (
    build_spec_diff_preview,
    canonical_spec_manifest_digest,
    normalize_spec_path,
)
from app.runtime.models import (
    RuntimeJob,
    RuntimeJobStatus,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from tests.utils.user import authentication_token_from_email, create_random_user
from tests.utils.utils import random_lower_string


def _namespace(db: Session, admin_id: uuid.UUID) -> Namespace:
    namespace = Namespace.model_validate(
        NamespaceCreate(
            name=f"project-ns-{random_lower_string()}",
            code=f"project-{random_lower_string()}",
            admin_user_id=admin_id,
        ).model_dump(exclude={"admin_user_id"})
    )
    db.add(namespace)
    db.commit()
    db.refresh(namespace)
    crud.ensure_namespace_membership(
        session=db,
        user_id=admin_id,
        namespace_id=namespace.id,
        role=NamespaceRole.ADMIN,
    )
    return namespace


def test_spec_path_rejects_repository_escape() -> None:
    assert normalize_spec_path("context/specs") == "context/specs"
    for unsafe in ("/etc/passwd", "../context", "context/../secret", r"..\secret"):
        try:
            normalize_spec_path(unsafe)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError(f"unsafe path was accepted: {unsafe}")


def test_spec_diff_uses_runtime_materialized_content() -> None:
    repository = ProjectRepository(
        project_id=uuid.uuid4(),
        remote_url="https://example.test/spec.git",
        purpose="Spec",
        validated_commit="c" * 40,
    )
    location = ProjectSpecLocation(
        project_id=repository.project_id,
        repository_id=repository.id,
        path="context/specs",
        location_type=SpecLocationType.DIRECTORY,
        description="规范",
    )
    manifest = {
        "required_files": ["README.md", "missing.md"],
        "templates": {
            "README.md": "# New\n",
            "new.md": "new file\n",
        },
    }
    version = SpecStandardVersion(
        standard_id=uuid.uuid4(),
        version="1.0.0",
        manifest=manifest,
        content_digest=canonical_spec_manifest_digest(manifest),
    )
    runtime_id = uuid.uuid4()
    preview = build_spec_diff_preview(
        location=location,
        repository=repository,
        version=version,
        runtime_id=runtime_id,
        proof={
            "runtime_job_id": str(uuid.uuid4()),
            "spec_locations": [
                {
                    "spec_location_id": str(location.id),
                    "files": [
                        {
                            "path": "context/specs/README.md",
                            "content": "# Old\n",
                            "content_digest": "d" * 64,
                        }
                    ],
                }
            ],
        },
    )
    actions = {item["template_path"]: item["action"] for item in preview["files"]}
    assert actions == {
        "README.md": "modify",
        "missing.md": "missing_template",
        "new.md": "add",
    }
    readme = next(
        item for item in preview["files"] if item["template_path"] == "README.md"
    )
    assert "-# Old" in readme["patch"]
    assert "+# New" in readme["patch"]
    assert preview["has_conflicts"] is True


def test_project_configuration_is_member_scoped_and_archive_is_read_only(
    client: TestClient, db: Session
) -> None:
    admin = create_random_user(db)
    member = create_random_user(db)
    namespace = _namespace(db, admin.id)
    crud.ensure_namespace_membership(
        session=db,
        user_id=member.id,
        namespace_id=namespace.id,
        role=NamespaceRole.USER,
    )
    headers = {
        **authentication_token_from_email(client=client, email=admin.email, db=db),
        "X-Namespace-Id": str(namespace.id),
    }
    created = client.post(
        f"{settings.API_V1_STR}/projects",
        headers=headers,
        json={
            "slug": "neo-workspace",
            "name": "Neo Workspace",
            "member_ids": [str(member.id)],
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert set(project["member_ids"]) == {str(admin.id), str(member.id)}

    repository = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/repositories",
        headers=headers,
        json={
            "remote_url": "https://example.test/team/repo.git",
            "purpose": "业务代码与项目规范",
        },
    )
    assert repository.status_code == 201, repository.text

    member_headers = {
        **authentication_token_from_email(client=client, email=member.email, db=db),
        "X-Namespace-Id": str(namespace.id),
    }
    forbidden = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/repositories",
        headers=member_headers,
        json={
            "remote_url": "https://example.test/team/forbidden.git",
            "purpose": "普通成员不得修改配置",
        },
    )
    assert forbidden.status_code == 403

    second_repository = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/repositories",
        headers=headers,
        json={
            "remote_url": "https://example.test/team/docs.git",
            "purpose": "跨项目规范与文档",
        },
    )
    assert second_repository.status_code == 201, second_repository.text

    first_location = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/spec-locations",
        headers=headers,
        json={
            "repository_id": repository.json()["id"],
            "path": "context/specs",
            "location_type": "directory",
            "description": "项目规范",
        },
    )
    assert first_location.status_code == 201, first_location.text
    second_location = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/spec-locations",
        headers=headers,
        json={
            "repository_id": second_repository.json()["id"],
            "path": "standards",
            "location_type": "directory",
            "description": "共享标准",
        },
    )
    assert second_location.status_code == 201, second_location.text

    standard = client.post(
        f"{settings.API_V1_STR}/spec-standards",
        headers=headers,
        json={
            "scope_type": "namespace",
            "slug": "delivery-standard",
            "name": "交付标准",
        },
    )
    assert standard.status_code == 201, standard.text
    manifest_v1 = {
        "required_files": ["README.md"],
        "templates": {"README.md": "# V1\n"},
    }
    version_v1 = client.post(
        f"{settings.API_V1_STR}/spec-standards/{standard.json()['id']}/versions",
        headers=headers,
        json={
            "version": "1.0.0",
            "manifest": manifest_v1,
            "content_digest": canonical_spec_manifest_digest(manifest_v1),
        },
    )
    assert version_v1.status_code == 201, version_v1.text
    bound = client.put(
        f"{settings.API_V1_STR}/projects/{project['id']}/spec-locations/"
        f"{first_location.json()['id']}/binding",
        headers=headers,
        json={"standard_version_id": version_v1.json()["id"]},
    )
    assert bound.status_code == 200, bound.text

    manifest_v2 = {
        "required_files": ["README.md"],
        "templates": {"README.md": "# V2\n"},
    }
    version_v2 = client.post(
        f"{settings.API_V1_STR}/spec-standards/{standard.json()['id']}/versions",
        headers=headers,
        json={
            "version": "2.0.0",
            "manifest": manifest_v2,
            "content_digest": canonical_spec_manifest_digest(manifest_v2),
        },
    )
    assert version_v2.status_code == 201, version_v2.text
    locations = client.get(
        f"{settings.API_V1_STR}/projects/{project['id']}/spec-locations",
        headers=headers,
    )
    assert locations.status_code == 200
    assert locations.json()["count"] == 2
    persisted_binding = next(
        item["binding"]
        for item in locations.json()["data"]
        if item["id"] == first_location.json()["id"]
    )
    assert persisted_binding["standard_version_id"] == version_v1.json()["id"]
    assert persisted_binding["standard_version_id"] != version_v2.json()["id"]

    escaped = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/spec-locations",
        headers=headers,
        json={
            "repository_id": repository.json()["id"],
            "path": "../outside",
            "location_type": "directory",
            "description": "非法位置",
        },
    )
    assert escaped.status_code == 422

    archived = client.patch(
        f"{settings.API_V1_STR}/projects/{project['id']}",
        headers=headers,
        json={"archive": True},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    mutation = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/repositories",
        headers=headers,
        json={
            "remote_url": "https://example.test/team/other.git",
            "purpose": "不应被接受",
        },
    )
    assert mutation.status_code == 409


def test_repository_validation_is_runtime_queued_and_proof_is_persisted(
    client: TestClient, db: Session
) -> None:
    admin = create_random_user(db)
    namespace = _namespace(db, admin.id)
    remote_url = "https://example.test/team/runtime-verified.git"
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type=RuntimeType.PLATFORM,
        route_mode=RuntimeRouteMode.PLATFORM_GATEWAY,
        model_id="repository-probe-model",
        config={
            "compatibility_verified": True,
            "allowed_working_roots": ["/workspaces"],
            "repository_workspaces": {
                remote_url: {
                    "workspace_ref": "workspace://runtime-verified",
                    "workspace_path": "/workspaces/runtime-verified",
                }
            },
        },
    )
    db.add(runtime)
    db.commit()
    headers = {
        **authentication_token_from_email(client=client, email=admin.email, db=db),
        "X-Namespace-Id": str(namespace.id),
    }
    project = client.post(
        f"{settings.API_V1_STR}/projects",
        headers=headers,
        json={
            "slug": "runtime-verified-project",
            "name": "Runtime Verified Project",
            "default_runtime_id": str(runtime.id),
        },
    ).json()
    repository = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/repositories",
        headers=headers,
        json={"remote_url": remote_url, "purpose": "受控验证仓库"},
    ).json()
    queued = client.post(
        f"{settings.API_V1_STR}/projects/{project['id']}/repositories/{repository['id']}/validate",
        headers=headers,
        json={"runtime_id": str(runtime.id)},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "unvalidated"
    job = db.get(RuntimeJob, uuid.UUID(queued.json()["validation_job_id"]))
    assert job is not None
    assert job.payload["workspace_path"] == "/workspaces/runtime-verified"
    assert job.payload["allowed_roots"] == ["/workspaces"]
    job.status = RuntimeJobStatus.SUCCEEDED
    job.result = {
        "workspace_ref": "workspace://runtime-verified",
        "workspace_path": "/workspaces/runtime-verified",
        "remote_url": remote_url,
        "commit": "b" * 40,
        "spec_locations": [],
    }
    job.completed_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    listed = client.get(
        f"{settings.API_V1_STR}/projects/{project['id']}/repositories",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    persisted = listed.json()["data"][0]
    assert persisted["status"] == "available"
    assert persisted["validated_commit"] == "b" * 40
    proof = persisted["runtime_workspace_refs"][str(runtime.id)]
    assert proof["runtime_job_id"] == str(job.id)
    assert proof["workspace_ref"] == "workspace://runtime-verified"
