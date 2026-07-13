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
)
from app.core.config import settings
from app.models import Namespace, NamespaceCreate, NamespaceRole
from app.runtime.models import RuntimeProfile, RuntimeRouteMode, RuntimeType
from tests.utils.user import authentication_token_from_email, create_random_user
from tests.utils.utils import random_lower_string


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
