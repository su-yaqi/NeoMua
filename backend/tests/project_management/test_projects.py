import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import Namespace, NamespaceCreate, NamespaceRole
from app.project_management.service import normalize_spec_path
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
