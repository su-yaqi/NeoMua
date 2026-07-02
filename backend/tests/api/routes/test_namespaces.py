import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import (
    Namespace,
    NamespaceCreate,
    NamespaceRole,
)
from tests.utils.user import authentication_token_from_email, create_random_user
from tests.utils.utils import random_email, random_lower_string


def create_namespace(
    db: Session,
    *,
    is_active: bool = True,
    admin_user_id: uuid.UUID | None = None,
) -> Namespace:
    namespace = Namespace.model_validate(
        NamespaceCreate(
            name=f"namespace-{random_lower_string()}",
            code=f"code-{random_lower_string()}",
            is_active=is_active,
            admin_user_id=admin_user_id,
        ).model_dump(exclude={"admin_user_id"})
    )
    db.add(namespace)
    db.commit()
    db.refresh(namespace)
    if admin_user_id:
        crud.ensure_namespace_membership(
            session=db,
            user_id=admin_user_id,
            namespace_id=namespace.id,
            role=NamespaceRole.ADMIN,
        )
    return namespace


def namespace_headers(
    auth_headers: dict[str, str], namespace_id: uuid.UUID
) -> dict[str, str]:
    return {**auth_headers, "X-Namespace-Id": str(namespace_id)}


def test_read_system_namespaces(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/platform/namespaces",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert "count" in body


def test_regular_users_only_receive_active_namespaces(
    client: TestClient, db: Session
) -> None:
    user = create_random_user(db)
    active_namespace = create_namespace(db, is_active=True)
    inactive_namespace = create_namespace(db, is_active=False)

    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=active_namespace.id,
        role=NamespaceRole.USER,
    )
    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=inactive_namespace.id,
        role=NamespaceRole.USER,
    )

    headers = authentication_token_from_email(client=client, email=user.email, db=db)
    response = client.get(f"{settings.API_V1_STR}/namespaces/mine", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    returned_ids = {item["id"] for item in body["data"]}
    assert str(active_namespace.id) in returned_ids
    assert str(inactive_namespace.id) not in returned_ids


def test_namespace_admin_cannot_manage_other_namespace(
    client: TestClient, db: Session
) -> None:
    admin_user = create_random_user(db)
    own_namespace = create_namespace(db, admin_user_id=admin_user.id)
    other_namespace = create_namespace(db)
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=admin_user.email, db=db),
        own_namespace.id,
    )
    response = client.get(
        f"{settings.API_V1_STR}/namespaces/{other_namespace.id}/users",
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Namespace admin privilege required"


def test_namespace_admin_cannot_remove_own_membership(
    client: TestClient, db: Session
) -> None:
    admin_user = create_random_user(db)
    namespace = create_namespace(db, admin_user_id=admin_user.id)

    headers = namespace_headers(
        authentication_token_from_email(client=client, email=admin_user.email, db=db),
        namespace.id,
    )
    response = client.delete(
        f"{settings.API_V1_STR}/namespaces/{namespace.id}/users/{admin_user.id}",
        headers=headers,
    )

    assert response.status_code == 403
    assert "cannot remove themselves" in response.json()["detail"]


def test_namespace_admin_cannot_downgrade_self(client: TestClient, db: Session) -> None:
    admin_user = create_random_user(db)
    namespace = create_namespace(db, admin_user_id=admin_user.id)

    headers = namespace_headers(
        authentication_token_from_email(client=client, email=admin_user.email, db=db),
        namespace.id,
    )
    response = client.patch(
        f"{settings.API_V1_STR}/namespaces/{namespace.id}/users/{admin_user.id}",
        headers=headers,
        json={"role": "user"},
    )

    assert response.status_code == 403
    assert "cannot change their own role" in response.json()["detail"]


def test_create_namespace_user_reuses_existing_platform_user(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    existing_user = create_random_user(db)

    response = client.post(
        f"{settings.API_V1_STR}/namespaces/{namespace.id}/users",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        json={
            "email": existing_user.email,
            "full_name": existing_user.full_name,
            "password": random_lower_string(),
            "is_active": True,
            "role": "developer",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(existing_user.id)
    assert body["namespace_roles"] == [
        {"namespace_id": str(namespace.id), "role": "developer"}
    ]


def test_delete_namespace_membership_keeps_platform_user(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    user = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=namespace.id,
        role=NamespaceRole.USER,
    )

    response = client.delete(
        f"{settings.API_V1_STR}/namespaces/{namespace.id}/users/{user.id}",
        headers=namespace_headers(superuser_token_headers, namespace.id),
    )

    assert response.status_code == 200
    assert crud.get_user_by_email(session=db, email=user.email) is not None


def test_create_platform_user_with_namespace_assignments(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    email = random_email()

    response = client.post(
        f"{settings.API_V1_STR}/platform/users",
        headers=superuser_token_headers,
        json={
            "email": email,
            "full_name": "Platform User",
            "password": random_lower_string(),
            "is_active": True,
            "is_superuser": False,
            "namespace_assignments": [
                {"namespace_id": str(namespace.id), "role": "admin"}
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["namespace_roles"] == [
        {"namespace_id": str(namespace.id), "role": "admin"}
    ]


def test_update_platform_user_returns_conflict_for_duplicate_email(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    first_user = create_random_user(db)
    second_user = create_random_user(db)

    response = client.patch(
        f"{settings.API_V1_STR}/platform/users/{second_user.id}",
        headers=superuser_token_headers,
        json={"email": first_user.email},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "User with this email already exists"


def test_superuser_can_read_members_of_inactive_namespace(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db, is_active=False)
    user = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=namespace.id,
        role=NamespaceRole.USER,
    )

    response = client.get(
        f"{settings.API_V1_STR}/namespaces/{namespace.id}/users",
        headers=namespace_headers(superuser_token_headers, namespace.id),
    )

    assert response.status_code == 200
    assert response.json()["count"] >= 1
