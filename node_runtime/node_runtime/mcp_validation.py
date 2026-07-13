import hashlib
import json
from typing import Any

from node_runtime.protocol import Envelope, envelope
from node_runtime.secrets import read_node_secret
from runtime_worker.mcp_manager import McpRuntimeManager


class NodeMcpValidationController:
    def __init__(self, node_id: str, manager: McpRuntimeManager | None = None) -> None:
        self.node_id = node_id
        self.manager = manager or McpRuntimeManager()

    async def handle(self, message: Envelope) -> list[Envelope]:
        if message.type != "mcp_validation":
            return []
        payload = message.payload
        secret_ref = payload.get("secret_ref")
        try:
            if not isinstance(secret_ref, str) or not secret_ref:
                raise RuntimeError("Node MCP validation requires a local secret_ref")
            secret_inputs = read_node_secret(secret_ref)
            command: dict[str, Any] = {
                "transport": payload["transport"],
                "config": payload["config"],
                "protocol_version": payload["protocol_version"],
                "secret_inputs": secret_inputs,
            }
            tools = await self.manager.validate(command)
            canonical_secret = json.dumps(
                secret_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            result = {
                "status": "verified",
                "capability_fingerprint": payload["capability_fingerprint"],
                "secret_fingerprint": hashlib.sha256(canonical_secret).hexdigest(),
                "tools": tools,
            }
        except Exception as exc:
            result = {
                "status": "failed",
                "capability_fingerprint": payload.get("capability_fingerprint", ""),
                "error": {"code": "mcp_validation_failed", "message": str(exc)},
                "tools": [],
            }
        return [
            envelope(
                "mcp_validation_result",
                self.node_id,
                {"attempt_id": payload.get("attempt_id"), **result},
            )
        ]
