import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel


class Envelope(BaseModel):
    type: str
    protocol_version: Literal["1"] = "1"
    message_id: uuid.UUID
    correlation_id: uuid.UUID | None = None
    node_id: uuid.UUID
    sent_at: datetime
    payload: dict[str, Any]


def envelope(message_type: str, node_id: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(
        type=message_type,
        message_id=uuid.uuid4(),
        node_id=uuid.UUID(node_id),
        sent_at=datetime.now(timezone.utc),
        payload=payload,
    )
