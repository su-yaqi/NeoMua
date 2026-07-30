import uuid
from datetime import datetime, timezone

import pytest

from node_runtime.mcp_validation import NodeMcpValidationController
from node_runtime.protocol import Envelope


class FakeManager:
    async def validate(self, command):
        assert command["secret_inputs"] == {"Authorization": "secret"}
        return [{"name": "echo", "description": "", "input_schema": {}}]


@pytest.mark.anyio
async def test_node_mcp_validation_reads_only_local_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        "node_runtime.mcp_validation.read_node_secret",
        lambda ref: {"Authorization": "secret"},
    )
    message = Envelope(
        type="mcp_validation",
        protocol_version="2",
        message_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        sent_at=datetime.now(timezone.utc),
        payload={
            "attempt_id": str(uuid.uuid4()),
            "transport": "streamable_http",
            "config": {"endpoint": "https://example.com/mcp"},
            "protocol_version": "2025-06-18",
            "secret_ref": "local-ref",
            "capability_fingerprint": "fingerprint",
        },
    )
    controller = NodeMcpValidationController(
        str(message.node_id), manager=FakeManager()
    )
    responses = await controller.handle(message)
    assert responses[0].type == "mcp_validation_result"
    assert responses[0].payload["status"] == "verified"
    assert "secret_inputs" not in responses[0].payload
