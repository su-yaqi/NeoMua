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
    ) -> None:
        self.url = websocket_url(platform_url)
        self.identity = identity
        self.reconcile_state = reconcile_state
        self.on_message = on_message
        self.identity_store = identity_store

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
                heartbeat = asyncio.create_task(self._heartbeat_loop(websocket))
                receiver = asyncio.create_task(self._receive_loop(websocket))
                done, pending = await asyncio.wait(
                    {heartbeat, receiver}, return_when=asyncio.FIRST_COMPLETED
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
            if self.on_message:
                await self.on_message(message)

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
