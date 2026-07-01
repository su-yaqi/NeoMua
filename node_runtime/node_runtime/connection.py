import asyncio
import base64
import json
import random
import time
from collections.abc import Awaitable, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from node_runtime.identity import DeviceIdentity
from node_runtime.identity import IdentityStore
from node_runtime.protocol import Envelope, envelope
from node_runtime.reconcile import ReconcileState
from node_runtime.tasks import NodeTaskController
from node_runtime.runtime_config import RuntimeConfigManager
from node_runtime.artifacts.controller import ArtifactController


class PermanentConnectionError(RuntimeError):
    pass


def handshake_headers(
    identity: DeviceIdentity, *, timestamp: int | None = None
) -> dict[str, str]:
    value = timestamp if timestamp is not None else int(time.time())
    private = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(identity.private_key)
    )
    signature = private.sign(f"neomua-ws-v1:{value}".encode())
    return {
        "Authorization": f"Bearer {identity.credential}",
        "X-Node-Timestamp": str(value),
        "X-Node-Signature": base64.b64encode(signature).decode(),
    }


def reconnect_delay(attempt: int, *, jitter: float | None = None) -> float:
    base = min(60.0, 2.0 ** min(attempt, 6))
    factor = random.uniform(0.8, 1.0) if jitter is None else 1.0 - 0.2 * jitter
    return min(60.0, base * factor)


def websocket_url(platform_url: str) -> str:
    if not platform_url.startswith("https://"):
        raise ValueError("platform URL must use HTTPS")
    return f"wss://{platform_url[8:].rstrip('/')}/api/v1/node/ws"


class NodeConnection:
    def __init__(
        self,
        platform_url: str,
        identity: DeviceIdentity,
        reconcile_state: ReconcileState,
        *,
        on_message: Callable[[Envelope], Awaitable[None]] | None = None,
        identity_store: IdentityStore | None = None,
        task_controller: NodeTaskController | None = None,
        runtime_config_manager: RuntimeConfigManager | None = None,
        artifact_controller: ArtifactController | None = None,
    ) -> None:
        self.url = websocket_url(platform_url)
        self.identity = identity
        self.reconcile_state = reconcile_state
        self.on_message = on_message
        self.identity_store = identity_store
        self.task_controller = task_controller
        self.runtime_config_manager = runtime_config_manager
        self.artifact_controller = artifact_controller

    async def connect_once(self) -> None:
        try:
            async with connect(
                self.url,
                additional_headers=handshake_headers(self.identity),
                open_timeout=30,
                ping_interval=None,
                max_size=8 * 1024 * 1024,
            ) as websocket:
                hello = Envelope.model_validate_json(
                    await asyncio.wait_for(websocket.recv(), timeout=30)
                )
                if hello.type != "hello_ack":
                    raise RuntimeError("server did not acknowledge node handshake")
                if self.identity.previous_credential_id:
                    await websocket.send(
                        envelope(
                            "credential_rotation_ack", self.identity.node_id,
                            {"previous_credential_id": self.identity.previous_credential_id},
                        ).model_dump_json()
                    )
                await websocket.send(
                    envelope(
                        "reconcile", self.identity.node_id,
                        self.reconcile_state.model_dump(mode="json"),
                    ).model_dump_json()
                )
                if self.task_controller:
                    await self.task_controller.replay_pending()
                heartbeat = asyncio.create_task(self._heartbeat_loop(websocket))
                receiver = asyncio.create_task(self._receive_loop(websocket))
                tasks = {heartbeat, receiver}
                if self.task_controller:
                    tasks.add(asyncio.create_task(self._task_outbox_loop(websocket)))
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
        except InvalidStatus as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                raise PermanentConnectionError(
                    "node credential was rejected; explicit re-enrollment is required"
                ) from exc
            raise

    async def _heartbeat_loop(self, websocket) -> None:
        while True:
            await asyncio.sleep(20)
            await websocket.send(
                envelope("heartbeat", self.identity.node_id, {}).model_dump_json()
            )

    async def _receive_loop(self, websocket) -> None:
        async for raw in websocket:
            message = Envelope.model_validate_json(raw)
            if message.type == "error":
                raise RuntimeError(json.dumps(message.payload))
            if message.type == "credential_rotation_required":
                await websocket.send(
                    envelope(
                        "rotate_credential", self.identity.node_id, {}
                    ).model_dump_json()
                )
                continue
            if message.type == "credential_rotated":
                if self.identity_store is None:
                    raise RuntimeError("identity store is required for credential rotation")
                self.identity.credential = str(message.payload["credential"])
                self.identity.previous_credential_id = str(
                    message.payload["previous_credential_id"]
                )
                self.identity_store.save(self.identity)
                return
            if message.type == "credential_rotation_acknowledged":
                if self.identity_store is None:
                    raise RuntimeError("identity store is required for credential rotation")
                self.identity.previous_credential_id = None
                self.identity_store.save(self.identity)
                continue
            if message.type == "runtime_config" and self.runtime_config_manager:
                try:
                    result = await self.runtime_config_manager.apply(message.payload)
                    self.reconcile_state.config_revision = int(result["revision"])
                    response_type = "runtime_config_applied"
                except ValueError as exc:
                    result = {
                        "revision": message.payload.get("revision"),
                        "runtime_id": message.payload.get("runtime_id"),
                        "reason": str(exc),
                    }
                    response_type = "runtime_config_rejected"
                await websocket.send(
                    envelope(response_type, self.identity.node_id, result).model_dump_json()
                )
                continue
            if self.artifact_controller:
                artifact_responses = await self.artifact_controller.handle(message)
                for response in artifact_responses:
                    await websocket.send(response.model_dump_json())
                if artifact_responses:
                    continue
            if self.task_controller:
                responses = await self.task_controller.handle(message)
                for response in responses:
                    await websocket.send(response.model_dump_json())
            if self.on_message:
                await self.on_message(message)

    async def _task_outbox_loop(self, websocket) -> None:
        assert self.task_controller is not None
        while True:
            message = await self.task_controller.outbox.get()
            await websocket.send(message.model_dump_json())

    async def run_forever(self) -> None:
        attempt = 0
        while True:
            try:
                await self.connect_once()
                attempt = 0
            except PermanentConnectionError:
                raise
            except (OSError, TimeoutError, InvalidStatus, RuntimeError):
                await asyncio.sleep(reconnect_delay(attempt))
                attempt += 1
