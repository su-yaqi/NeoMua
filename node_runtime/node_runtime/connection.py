import asyncio
import base64
import hashlib
import json
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from runtime_worker.agent_shell import RunCommand
from runtime_worker.runtime_configuration import RuntimeConfigurationStore
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from node_runtime.adapter_registry import public_installation
from node_runtime.agent_releases import AgentReleaseController
from node_runtime.artifacts.controller import ArtifactController
from node_runtime.identity import DeviceIdentity, IdentityStore, sign_device_payload
from node_runtime.mcp_validation import NodeMcpValidationController
from node_runtime.protocol import Envelope, envelope
from node_runtime.reconcile import ReconcileState
from node_runtime.runtime_config import RuntimeConfigManager
from node_runtime.skill_sync import NodeSkillSyncController
from node_runtime.tasks import NodeTaskController


class PermanentConnectionError(RuntimeError):
    pass


def handshake_headers(
    identity: DeviceIdentity, *, timestamp: int | None = None
) -> dict[str, str]:
    value = timestamp if timestamp is not None else int(time.time())
    nonce = secrets.token_urlsafe(32)
    private = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(identity.private_key)
    )
    signature = private.sign(f"neomua-ws-v1:{value}:{nonce}".encode())
    return {
        "Authorization": f"Bearer {identity.credential}",
        "X-Node-Protocol-Version": "2",
        "X-Node-Timestamp": str(value),
        "X-Node-Nonce": nonce,
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
        harness_capabilities: dict[str, Any] | None = None,
        secret_fingerprints: Callable[[], dict[str, str]] | None = None,
        agent_release_controller: AgentReleaseController | None = None,
        mcp_validation_controller: NodeMcpValidationController | None = None,
        skill_sync_controller: NodeSkillSyncController | None = None,
        runtime_installations: list[dict[str, Any]] | None = None,
        runtime_configuration_store: RuntimeConfigurationStore | None = None,
        on_discovery_generation: Callable[[int], None] | None = None,
        discovery_observations: list[dict[str, Any]] | None = None,
        adapter_registry_digest: str | None = None,
        discovery_provider: Callable[
            [], tuple[list[dict[str, Any]], list[dict[str, Any]]]
        ]
        | None = None,
    ) -> None:
        self.url = websocket_url(platform_url)
        self.identity = identity
        self.reconcile_state = reconcile_state
        self.on_message = on_message
        self.identity_store = identity_store
        self.task_controller = task_controller
        self.runtime_config_manager = runtime_config_manager
        self.artifact_controller = artifact_controller
        self.harness_capabilities = harness_capabilities
        self.secret_fingerprints = secret_fingerprints
        self.agent_release_controller = agent_release_controller
        self.mcp_validation_controller = mcp_validation_controller
        self.skill_sync_controller = skill_sync_controller
        self.runtime_installations = runtime_installations
        self.runtime_configuration_store = runtime_configuration_store
        self.on_discovery_generation = on_discovery_generation
        self.discovery_observations = discovery_observations or []
        self.adapter_registry_digest = adapter_registry_digest
        self.discovery_provider = discovery_provider

    async def _send_discovery_report(
        self,
        websocket,
        *,
        generation: int,
        installations: list[dict[str, Any]],
        observations: list[dict[str, Any]],
    ) -> None:
        discovery_payload = {
            "generation": generation,
            "installations": [public_installation(item) for item in installations],
            "observations": observations,
        }
        if self.adapter_registry_digest is not None:
            discovery_payload["adapter_registry_digest"] = self.adapter_registry_digest
        discovery_digest = hashlib.sha256(
            json.dumps(
                discovery_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        await websocket.send(
            envelope(
                "runtime_discovery_report",
                self.identity.node_id,
                {
                    **discovery_payload,
                    "device_signature": sign_device_payload(
                        self.identity.private_key,
                        f"neomua-runtime-discovery-v1:{discovery_digest}",
                    ),
                },
            ).model_dump_json()
        )

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
                            "credential_rotation_ack",
                            self.identity.node_id,
                            {
                                "previous_credential_id": self.identity.previous_credential_id
                            },
                        ).model_dump_json()
                    )
                await websocket.send(
                    envelope(
                        "reconcile",
                        self.identity.node_id,
                        self.reconcile_state.model_dump(mode="json"),
                    ).model_dump_json()
                )
                if self.runtime_installations is not None:
                    await self._send_discovery_report(
                        websocket,
                        generation=self.reconcile_state.discovery_generation + 1,
                        installations=self.runtime_installations,
                        observations=self.discovery_observations,
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
            payload: dict[str, Any] = {}
            if self.harness_capabilities is not None:
                payload["harness_capabilities"] = self.harness_capabilities
            if self.secret_fingerprints is not None:
                payload["mcp_secret_fingerprints"] = self.secret_fingerprints()
            if self.runtime_configuration_store is not None:
                payload["runtime_capability_evidence"] = (
                    self.runtime_configuration_store.capability_evidence()
                )
            await websocket.send(
                envelope(
                    "heartbeat",
                    self.identity.node_id,
                    payload,
                ).model_dump_json()
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
            if message.type == "runtime_discovery_refresh":
                if self.discovery_provider is None:
                    raise RuntimeError("Runtime discovery refresh is unavailable")
                installations, observations = await asyncio.to_thread(
                    self.discovery_provider
                )
                self.runtime_installations = installations
                self.discovery_observations = observations
                await self._send_discovery_report(
                    websocket,
                    generation=int(message.payload["generation"]),
                    installations=installations,
                    observations=observations,
                )
                continue
            if message.type == "runtime_discovery_ack":
                generation = int(message.payload["generation"])
                self.reconcile_state.discovery_generation = generation
                if self.on_discovery_generation is not None:
                    self.on_discovery_generation(generation)
                continue
            if message.type == "credential_rotated":
                if self.identity_store is None:
                    raise RuntimeError(
                        "identity store is required for credential rotation"
                    )
                self.identity.credential = str(message.payload["credential"])
                self.identity.previous_credential_id = str(
                    message.payload["previous_credential_id"]
                )
                self.identity_store.save(self.identity)
                return
            if message.type == "credential_rotation_acknowledged":
                if self.identity_store is None:
                    raise RuntimeError(
                        "identity store is required for credential rotation"
                    )
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
                    envelope(
                        response_type, self.identity.node_id, result
                    ).model_dump_json()
                )
            if message.type == "runtime_instance_configuration":
                payload = message.payload
                installation = next(
                    (
                        item
                        for item in (self.runtime_installations or [])
                        if item.get("installation_key")
                        == payload.get("installation_key")
                        and item.get("engine_type") == payload.get("engine_type")
                    ),
                    None,
                )
                try:
                    if installation is None:
                        raise ValueError("Runtime installation is unavailable")
                    if self.runtime_configuration_store is None:
                        raise ValueError("Runtime configuration store is unavailable")
                    applied = self.runtime_configuration_store.apply(
                        payload,
                        engine_version=installation.get("engine_version"),
                        adapter_version=str(installation.get("adapter_version")),
                        capabilities=dict(installation.get("capabilities", {})),
                        discovered_models=list(
                            installation.get("discovered_models", [])
                        ),
                        adapter_execution_ref=payload.get("adapter_execution_ref"),
                        local_execution_path=(
                            str(installation["_execution_path"])
                            if installation.get("_execution_path") is not None
                            else None
                        ),
                        local_executable_fingerprint=str(
                            installation.get("executable_fingerprint")
                        ),
                        local_revalidation_contract=(
                            dict(installation["_revalidation_contract"])
                            if installation.get("_revalidation_contract") is not None
                            else None
                        ),
                    )
                    result = {
                        "status": "applied",
                        "engine_version": applied.engine_version,
                        "adapter_version": applied.adapter_version,
                        "capabilities": applied.capabilities,
                        "discovered_models": applied.discovered_models,
                    }
                except (KeyError, TypeError, ValueError) as exc:
                    result = {
                        "status": "failed",
                        "engine_version": installation.get("engine_version")
                        if installation
                        else None,
                        "adapter_version": installation.get(
                            "adapter_version", "unknown"
                        )
                        if installation
                        else "unknown",
                        "error": {
                            "code": "runtime_configuration_apply_failed",
                            "message": str(exc),
                        },
                    }
                await websocket.send(
                    envelope(
                        "runtime_instance_configuration_result",
                        self.identity.node_id,
                        {
                            "runtime_instance_id": payload.get("runtime_instance_id"),
                            "configuration_revision_id": payload.get(
                                "configuration_revision_id"
                            ),
                            "configuration_digest": payload.get("configuration_digest"),
                            **result,
                        },
                    ).model_dump_json()
                )
                continue
            if message.type == "runtime_model_validation":
                payload = message.payload
                result: dict[str, Any]
                try:
                    if self.task_controller is None:
                        raise ValueError("Runtime engine controller is unavailable")
                    if self.runtime_configuration_store is None:
                        raise ValueError("Runtime configuration store is unavailable")
                    configuration = self.runtime_configuration_store.get(
                        str(payload["runtime_instance_id"])
                    )
                    if configuration is None:
                        raise ValueError("Runtime configuration is not applied locally")
                    terminal = False
                    event_count = 0
                    failure: str | None = None
                    command = RunCommand(
                        engine_type=str(payload["engine_type"]),
                        executable=configuration.execution_path(),
                        prompt="Reply with OK.",
                        model=str(payload["engine_model_id"]),
                        permission_mode="plan",
                        timeout_seconds=60,
                    )
                    async for event in self.task_controller.executor.shell.run_session(
                        command
                    ):
                        event_count += 1
                        if event.get("event_type") == "result":
                            terminal = True
                        elif event.get("event_type") == "error":
                            failure = str(
                                event.get("payload", {}).get("message", "failed")
                            )
                    if failure or not terminal:
                        raise ValueError(
                            failure or "Runtime engine returned no terminal result"
                        )
                    if self.runtime_configuration_store is None:
                        raise ValueError("Runtime configuration store is unavailable")
                    self.runtime_configuration_store.record_validated_model(
                        str(payload["runtime_instance_id"]),
                        str(payload["engine_model_id"]),
                        str(payload["route_key"]),
                    )
                    result = {
                        "status": "succeeded",
                        "evidence": {
                            "event_count": event_count,
                            "terminal_result": True,
                        },
                    }
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "error": {
                            "code": "runtime_native_model_validation_failed",
                            "message": str(exc),
                        },
                    }
                await websocket.send(
                    envelope(
                        "runtime_model_validation_result",
                        self.identity.node_id,
                        {
                            "runtime_model_binding_id": payload.get(
                                "runtime_model_binding_id"
                            ),
                            "attempt_no": payload.get("attempt_no"),
                            "engine_model_id": payload.get("engine_model_id"),
                            "route_key": payload.get("route_key"),
                            **result,
                        },
                    ).model_dump_json()
                )
                continue
                continue
            if self.artifact_controller:
                artifact_responses = await self.artifact_controller.handle(message)
                for response in artifact_responses:
                    if self.task_controller:
                        self.task_controller.outbox.put_nowait(response)
                    else:
                        await websocket.send(response.model_dump_json())
                if artifact_responses:
                    continue
            if self.agent_release_controller:
                release_responses = await self.agent_release_controller.handle(message)
                for response in release_responses:
                    if self.task_controller:
                        self.task_controller.outbox.put_nowait(response)
                    else:
                        await websocket.send(response.model_dump_json())
                if release_responses:
                    continue
            if self.skill_sync_controller:
                skill_responses = await self.skill_sync_controller.handle(message)
                for response in skill_responses:
                    if self.task_controller:
                        self.task_controller.outbox.put_nowait(response)
                    else:
                        await websocket.send(response.model_dump_json())
                if skill_responses:
                    continue
            if self.mcp_validation_controller:
                validation_responses = await self.mcp_validation_controller.handle(
                    message
                )
                for response in validation_responses:
                    if self.task_controller:
                        self.task_controller.outbox.put_nowait(response)
                    else:
                        await websocket.send(response.model_dump_json())
                if validation_responses:
                    continue
            if self.task_controller:
                responses = await self.task_controller.handle(message)
                for response in responses:
                    self.task_controller.outbox.put_nowait(response)
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
            except (
                OSError,
                TimeoutError,
                InvalidStatus,
                ConnectionClosed,
                WebSocketException,
                ValidationError,
                json.JSONDecodeError,
                RuntimeError,
            ):
                await asyncio.sleep(reconnect_delay(attempt))
                attempt += 1
