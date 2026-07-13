from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlmodel import Session

from app import crud
from app.conversation_management.models import (
    Conversation,
    ConversationAgent,
    ConversationAttachment,
    ConversationMessage,
    ConversationMode,
    ConversationStatus,
)
from app.conversation_management.service import append_conversation_event
from app.core.config import settings
from app.models import (
    LlmProviderConfig,
    LlmProviderModel,
    Namespace,
    NamespaceCreate,
    NamespaceRole,
    ProviderAuthType,
    ProviderModelSourceType,
)
from app.runtime.models import RuntimeProfile, RuntimeRouteMode, RuntimeType
from tests.utils.user import authentication_token_from_email, create_random_user
from tests.utils.utils import random_lower_string


def test_chat_uses_only_the_fixed_model_and_fails_closed(
    client: TestClient,
    db: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    user = create_random_user(db)
    other_member = create_random_user(db)
    namespace = Namespace.model_validate(
        NamespaceCreate(
            name=f"chat-ns-{random_lower_string()}",
            code=f"chat-{random_lower_string()}",
            admin_user_id=user.id,
        ).model_dump(exclude={"admin_user_id"})
    )
    db.add(namespace)
    db.commit()
    db.refresh(namespace)
    for member in (user, other_member):
        crud.ensure_namespace_membership(
            session=db,
            user_id=member.id,
            namespace_id=namespace.id,
            role=NamespaceRole.USER,
        )
    provider = LlmProviderConfig(
        namespace_id=namespace.id,
        config_name=f"chat-provider-{random_lower_string()}",
        provider_slug="custom",
        provider_display_name="Chat provider",
        auth_type=ProviderAuthType.API_KEY,
        base_url="https://model.example.test/v1",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(provider)
    db.flush()
    model = LlmProviderModel(
        provider_config_id=provider.id,
        model_id="fixed-chat-model",
        source_type=ProviderModelSourceType.MANUAL,
        is_enabled=True,
    )
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type=RuntimeType.PLATFORM,
        route_mode=RuntimeRouteMode.PLATFORM_GATEWAY,
        model_id=model.model_id,
        config={"compatibility_verified": True},
    )
    db.add_all([model, runtime])
    db.commit()

    gateway_requests: list[dict] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "id": "chat-response-1",
                "model": model.model_id,
                "content": [{"type": "text", "text": "固定模型回答"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
            gateway_requests.append({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(
        "app.conversation_management.routes.httpx.AsyncClient", FakeAsyncClient
    )
    headers = {
        **authentication_token_from_email(client=client, email=user.email, db=db),
        "X-Namespace-Id": str(namespace.id),
    }
    created = client.post(
        f"{settings.API_V1_STR}/conversations",
        headers={**headers, "Idempotency-Key": "fixed-chat-create"},
        json={
            "title": "固定模型 Chat",
            "mode": "chat",
            "runtime_id": str(runtime.id),
            "provider_config_id": str(provider.id),
            "model_id": model.model_id,
        },
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    private_headers = {
        **authentication_token_from_email(
            client=client, email=other_member.email, db=db
        ),
        "X-Namespace-Id": str(namespace.id),
    }
    assert (
        client.get(
            f"{settings.API_V1_STR}/conversations/{conversation_id}",
            headers=private_headers,
        ).status_code
        == 403
    )
    immutable_route = client.patch(
        f"{settings.API_V1_STR}/conversations/{conversation_id}",
        headers=headers,
        json={"model_id": "replacement-model"},
    )
    assert immutable_route.status_code == 422

    sent = client.post(
        f"{settings.API_V1_STR}/conversations/{conversation_id}/messages",
        headers={**headers, "Idempotency-Key": "fixed-chat-message-1"},
        json={"content": "仅使用固定模型", "target_type": "model"},
    )
    assert sent.status_code == 202, sent.text
    assert sent.json()["response"]["payload"]["content"] == "固定模型回答"
    assert gateway_requests[0]["json"]["model"] == model.model_id
    assert "tools" not in gateway_requests[0]["json"]
    assert "tool_choice" not in gateway_requests[0]["json"]

    model.is_enabled = False
    db.add(model)
    db.commit()
    stopped = client.post(
        f"{settings.API_V1_STR}/conversations/{conversation_id}/messages",
        headers={**headers, "Idempotency-Key": "fixed-chat-message-2"},
        json={"content": "目标失效后不得降级", "target_type": "model"},
    )
    assert stopped.status_code == 409
    assert len(gateway_requests) == 1


def test_roundtable_and_message_constraints_are_present() -> None:
    agent_indexes = {index.name: index for index in ConversationAgent.__table__.indexes}
    assert agent_indexes["uq_conversation_main_agent"].unique
    message_constraints = {
        constraint.name for constraint in ConversationMessage.__table__.constraints
    }
    assert "uq_conversation_message_sequence" in message_constraints
    assert "uq_conversation_message_idempotency" in message_constraints
    attachment_constraints = {
        constraint.name for constraint in ConversationAttachment.__table__.constraints
    }
    assert "uq_conversation_attachment_content" in attachment_constraints


def test_attachment_upload_uses_strict_text_scan(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ARTIFACT_LOCAL_ROOT", str(tmp_path / "artifacts"))
    user = create_random_user(db)
    namespace = Namespace.model_validate(
        NamespaceCreate(
            name=f"conversation-ns-{random_lower_string()}",
            code=f"conversation-{random_lower_string()}",
            admin_user_id=user.id,
        ).model_dump(exclude={"admin_user_id"})
    )
    db.add(namespace)
    db.commit()
    db.refresh(namespace)
    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=namespace.id,
        role=NamespaceRole.USER,
    )
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type=RuntimeType.PLATFORM,
        route_mode=RuntimeRouteMode.PLATFORM_GATEWAY,
        model_id="fixed-model",
        config={"compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    conversation = Conversation(
        namespace_id=namespace.id,
        creator_id=user.id,
        title="Attachment scan",
        mode=ConversationMode.CHAT,
        runtime_id=runtime.id,
        idempotency_key="attachment-test",
        creation_fingerprint="a" * 64,
    )
    db.add(conversation)
    db.commit()
    headers = {
        **authentication_token_from_email(client=client, email=user.email, db=db),
        "X-Namespace-Id": str(namespace.id),
    }
    uploaded = client.post(
        f"{settings.API_V1_STR}/conversations/{conversation.id}/attachments",
        headers=headers,
        files={"file": ("requirements.md", "# Goal\nSafe text", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["scan_status"] == "clean"
    assert "storage_ref" not in uploaded.json()

    unsafe = client.post(
        f"{settings.API_V1_STR}/conversations/{conversation.id}/attachments",
        headers=headers,
        files={"file": ("unsafe.txt", b"safe\x00payload", "text/plain")},
    )
    assert unsafe.status_code == 422


def test_conversation_sse_resumes_from_last_event_id(
    client: TestClient, db: Session
) -> None:
    user = create_random_user(db)
    namespace = Namespace.model_validate(
        NamespaceCreate(
            name=f"conversation-events-{random_lower_string()}",
            code=f"conversation-events-{random_lower_string()}",
            admin_user_id=user.id,
        ).model_dump(exclude={"admin_user_id"})
    )
    db.add(namespace)
    db.commit()
    db.refresh(namespace)
    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=namespace.id,
        role=NamespaceRole.USER,
    )
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type=RuntimeType.PLATFORM,
        route_mode=RuntimeRouteMode.PLATFORM_GATEWAY,
        model_id="fixed-model",
        config={"compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    conversation = Conversation(
        namespace_id=namespace.id,
        creator_id=user.id,
        title="Event recovery",
        mode=ConversationMode.CHAT,
        status=ConversationStatus.ARCHIVED,
        runtime_id=runtime.id,
        idempotency_key="event-recovery",
        creation_fingerprint="b" * 64,
    )
    db.add(conversation)
    db.flush()
    first = append_conversation_event(
        db, conversation.id, "message_created", {"message_id": "first"}
    )
    second = append_conversation_event(
        db, conversation.id, "message_updated", {"message_id": "second"}
    )
    db.commit()
    headers = {
        **authentication_token_from_email(client=client, email=user.email, db=db),
        "X-Namespace-Id": str(namespace.id),
    }

    listed = client.get(
        f"{settings.API_V1_STR}/conversations/{conversation.id}/events",
        headers=headers,
        params={"after_sequence": first.sequence},
    )
    assert listed.status_code == 200, listed.text
    assert [event["sequence"] for event in listed.json()["data"]] == [second.sequence]

    streamed = client.get(
        f"{settings.API_V1_STR}/conversations/{conversation.id}/events",
        headers={
            **headers,
            "Accept": "text/event-stream",
            "Last-Event-ID": str(first.sequence),
        },
    )
    assert streamed.status_code == 200, streamed.text
    assert f"id: {second.sequence}\n" in streamed.text
    assert "event: message_updated\n" in streamed.text
    assert '"message_id": "second"' in streamed.text
    assert f"id: {first.sequence}\n" not in streamed.text

    invalid_cursor = client.get(
        f"{settings.API_V1_STR}/conversations/{conversation.id}/events",
        headers={
            **headers,
            "Accept": "text/event-stream",
            "Last-Event-ID": "not-an-integer",
        },
    )
    assert invalid_cursor.status_code == 400
