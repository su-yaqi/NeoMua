from fastapi.testclient import TestClient

from app.core.config import settings


def test_internal_endpoint_rejects_user_jwt(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/events",
        headers=superuser_token_headers,
        json={"task_id": "00000000-0000-0000-0000-000000000000", "events": []},
    )
    assert response.status_code == 403
