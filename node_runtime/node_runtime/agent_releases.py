from pathlib import Path

from node_runtime.protocol import Envelope, envelope
from runtime_worker.release_store import AgentReleaseStore, canonical_bytes
import hashlib


class AgentReleaseController:
    def __init__(self, node_id: str, root: Path) -> None:
        self.node_id = node_id
        self.store = AgentReleaseStore(root)

    async def handle(self, message: Envelope) -> list[Envelope]:
        if message.type != "agent_release_deploy":
            return []
        payload = message.payload
        fingerprint = hashlib.sha256(
            canonical_bytes(payload["capability_inventory"])
        ).hexdigest()
        try:
            result = self.store.apply(payload)
            body = {
                "deployment_id": payload["deployment_id"],
                "status": "applied",
                **result,
                "capability_fingerprint": fingerprint,
            }
        except (OSError, ValueError, KeyError) as exc:
            body = {
                "deployment_id": payload.get("deployment_id"),
                "status": "failed",
                "resolved_spec_digest": payload.get("release", {}).get(
                    "resolved_spec_digest", "0" * 64
                ),
                "materialization_digest": payload.get("materialization", {}).get(
                    "resolved_spec_digest", "0" * 64
                ),
                "capability_fingerprint": fingerprint,
                "error": {"code": "agent_release_apply_failed", "message": str(exc)},
            }
        return [envelope("agent_release_result", self.node_id, body)]
