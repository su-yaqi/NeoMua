import asyncio
import hashlib
import json
import logging
import random
import time
from typing import Any

import httpx
from workflow_runtime.executor import execute_runtime_job

from runtime_worker.agent_shell import AgentShell, RunCommand
from runtime_worker.mcp_manager import McpRuntimeManager
from runtime_worker.release_store import AgentReleaseStore, canonical_bytes

logger = logging.getLogger(__name__)


class PermanentWorkerError(RuntimeError):
    """A configuration or identity failure that requires operator action."""


class TransientWorkerError(RuntimeError):
    """A recoverable control-plane or protocol failure."""


class TaskOwnershipLost(RuntimeError):
    """The current worker no longer owns the claimed task."""


class RuntimeWorker:
    def __init__(
        self,
        client: httpx.AsyncClient,
        internal_token: str,
        worker_id: str,
        *,
        shell: Any | None = None,
        lease_interval: float = 60,
        max_backoff: float = 60,
        interrupt_grace: float = 10,
        harness_capabilities: dict[str, Any] | None = None,
        capability_report_interval: float = 60,
        release_store: AgentReleaseStore | None = None,
        mcp_manager: McpRuntimeManager | None = None,
    ) -> None:
        self.client = client
        self.headers = {"X-Runtime-Token": internal_token}
        self.worker_id = worker_id
        self.shell = shell or AgentShell()
        self.lease_interval = lease_interval
        self.max_backoff = max_backoff
        self.interrupt_grace = interrupt_grace
        self.harness_capabilities = harness_capabilities
        self.capability_report_interval = capability_report_interval
        self._last_capability_report = 0.0
        self.release_store = release_store
        self.mcp_manager = mcp_manager

    async def validate_mcp_once(self) -> bool:
        if self.mcp_manager is None:
            return False
        try:
            response = await self.client.post(
                "/api/v1/internal/runtime/mcp-validations/claim",
                headers=self.headers,
            )
        except httpx.TransportError as exc:
            raise TransientWorkerError("MCP validation claim failed") from exc
        if response.status_code == 204:
            return False
        self._classify_response(response)
        command = response.json()
        fingerprint = hashlib.sha256(
            canonical_bytes(command["capability_inventory"])
        ).hexdigest()
        try:
            tools = await self.mcp_manager.validate(command)
            body: dict[str, Any] = {
                "status": "verified",
                "capability_fingerprint": fingerprint,
                "tools": tools,
            }
        except Exception as exc:
            body = {
                "status": "failed",
                "capability_fingerprint": fingerprint,
                "error": {"code": "mcp_validation_failed", "message": str(exc)},
                "tools": [],
            }
        reported = await self.client.post(
            f"/api/v1/internal/runtime/mcp-validations/{command['attempt_id']}/result",
            headers=self.headers,
            json=body,
        )
        self._classify_response(reported)
        return True

    async def apply_release_once(self) -> bool:
        if self.release_store is None:
            return False
        try:
            response = await self.client.post(
                "/api/v1/internal/runtime/agent-deployments/claim",
                headers=self.headers,
            )
        except httpx.TransportError as exc:
            raise TransientWorkerError("Agent Release claim failed") from exc
        if response.status_code == 204:
            return False
        self._classify_response(response)
        payload = response.json()
        deployment_id = payload["deployment_id"]
        capability_fingerprint = hashlib.sha256(
            canonical_bytes(payload["capability_inventory"])
        ).hexdigest()
        try:
            result = self.release_store.apply(payload)
            body: dict[str, Any] = {
                "status": "applied",
                **result,
                "capability_fingerprint": capability_fingerprint,
            }
        except (OSError, ValueError, KeyError) as exc:
            body = {
                "status": "failed",
                "resolved_spec_digest": payload["release"]["resolved_spec_digest"],
                "materialization_digest": payload["materialization"].get(
                    "resolved_spec_digest", "0" * 64
                ),
                "capability_fingerprint": capability_fingerprint,
                "error": {"code": "agent_release_apply_failed", "message": str(exc)},
            }
        reported = await self.client.post(
            f"/api/v1/internal/runtime/agent-deployments/{deployment_id}/result",
            headers=self.headers,
            json=body,
        )
        self._classify_response(reported)
        return True

    async def report_capabilities(self) -> None:
        if self.harness_capabilities is None:
            return
        try:
            response = await self.client.post(
                "/api/v1/internal/runtime/capabilities",
                headers=self.headers,
                json={
                    "worker_id": self.worker_id,
                    "harness_capabilities": self.harness_capabilities,
                },
            )
        except httpx.TransportError as exc:
            raise TransientWorkerError("capability report failed") from exc
        self._classify_response(response)
        self._last_capability_report = time.monotonic()

    async def run_runtime_job_once(self) -> bool:
        try:
            response = await self.client.post(
                "/api/v1/internal/runtime/jobs/claim",
                headers=self.headers,
                json={"worker_id": self.worker_id},
            )
        except httpx.TransportError as exc:
            raise TransientWorkerError("Runtime job claim failed") from exc
        if response.status_code == 204:
            return False
        self._classify_response(response)
        command = response.json()
        job_id = str(command["job_id"])
        revision = int(command["revision"])
        side_effecting = bool(command.get("side_effecting", False))
        stop_lease = asyncio.Event()
        lease = asyncio.create_task(
            self._renew_runtime_job_lease(job_id, revision, stop_lease)
        )
        try:
            try:
                result = await asyncio.to_thread(
                    execute_runtime_job,
                    str(command["kind"]),
                    dict(command["payload"]),
                )
                body: dict[str, Any] = {"status": "succeeded", "result": result}
            except Exception as exc:
                body = {
                    "status": (
                        "needs_manual_resolution" if side_effecting else "failed"
                    ),
                    "error": {
                        "code": "runtime_job_execution_failed",
                        "message": str(exc),
                    },
                }
            reported = await self.client.post(
                f"/api/v1/internal/runtime/jobs/{job_id}/result",
                headers=self.headers,
                json={"worker_id": self.worker_id, "revision": revision, **body},
            )
            self._classify_response(reported, task_scoped=True)
        finally:
            stop_lease.set()
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
        return True

    async def _renew_runtime_job_lease(
        self, job_id: str, revision: int, stopped: asyncio.Event
    ) -> None:
        while not stopped.is_set():
            await asyncio.sleep(self.lease_interval)
            response = await self.client.post(
                f"/api/v1/internal/runtime/jobs/{job_id}/lease",
                headers=self.headers,
                json={"worker_id": self.worker_id, "revision": revision},
            )
            self._classify_response(response, task_scoped=True)

    @staticmethod
    def _classify_response(
        response: httpx.Response, *, task_scoped: bool = False
    ) -> None:
        if response.status_code in {401, 403}:
            raise PermanentWorkerError("runtime service credential was rejected")
        if task_scoped and response.status_code in {404, 409}:
            raise TaskOwnershipLost("task lease or ownership was lost")
        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            raise TransientWorkerError(
                f"control plane returned HTTP {response.status_code}"
            )
        response.raise_for_status()

    async def _post_event(self, task_id: str, event: dict[str, Any]) -> None:
        for attempt in range(3):
            try:
                posted = await self.client.post(
                    "/api/v1/internal/runtime/events",
                    headers=self.headers,
                    json={"task_id": task_id, "events": [event]},
                )
                self._classify_response(posted, task_scoped=True)
                return
            except (httpx.TransportError, TransientWorkerError) as exc:
                if attempt == 2:
                    raise TransientWorkerError(
                        "event delivery retries exhausted"
                    ) from exc
                await asyncio.sleep(min(2**attempt, 4))

    async def _request_tool_approval(
        self,
        task_id: str,
        task_revision: int,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> bool:
        redacted = {
            key: "[REDACTED]"
            if key.lower()
            in {"api_key", "token", "password", "secret", "authorization"}
            else value
            for key, value in tool_input.items()
        }
        args_digest = hashlib.sha256(
            json.dumps(
                tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        created = await self.client.post(
            "/api/v1/internal/runtime/tool-approvals",
            headers=self.headers,
            json={
                "task_id": task_id,
                "task_revision": task_revision,
                "tool_call_id": f"{tool_name}:{args_digest[:16]}",
                "tool_qualified_name": tool_name,
                "redacted_args": redacted,
                "args_digest": args_digest,
                "expires_in_seconds": 300,
            },
        )
        self._classify_response(created, task_scoped=True)
        approval_id = created.json()["id"]
        while True:
            await asyncio.sleep(2)
            checked = await self.client.get(
                f"/api/v1/internal/runtime/tool-approvals/{approval_id}",
                headers=self.headers,
            )
            self._classify_response(checked, task_scoped=True)
            status = checked.json()["status"]
            if status == "approved":
                return True
            if status in {"denied", "expired", "cancelled"}:
                return False

    async def _request_agent_delegation(
        self,
        task_id: str,
        task_revision: int,
        target_conversation_agent_id: str,
        content: str,
    ) -> dict[str, Any]:
        created = await self.client.post(
            "/api/v1/internal/runtime/agent-delegations",
            headers=self.headers,
            json={
                "source_task_id": task_id,
                "source_task_revision": task_revision,
                "target_conversation_agent_id": target_conversation_agent_id,
                "content": content,
            },
        )
        self._classify_response(created, task_scoped=True)
        delegation_id = created.json()["id"]
        while True:
            checked = await self.client.get(
                f"/api/v1/internal/runtime/agent-delegations/{delegation_id}",
                headers=self.headers,
                params={
                    "source_task_id": task_id,
                    "source_task_revision": task_revision,
                },
            )
            self._classify_response(checked, task_scoped=True)
            result = checked.json()
            if result["status"] in {"completed", "failed"}:
                return result
            await asyncio.sleep(2)

    async def run_once(self) -> bool:
        try:
            response = await self.client.post(
                "/api/v1/internal/runtime/tasks/claim",
                headers=self.headers,
                json={"worker_id": self.worker_id},
            )
        except httpx.TransportError as exc:
            raise TransientWorkerError("claim request failed") from exc
        if response.status_code == 204:
            return False
        self._classify_response(response)
        try:
            payload = response.json()
            command = RunCommand(**payload["command"])
            task_id = str(payload["task_id"])
            revision = int(payload["revision"])
            command.task_revision = revision
            command.approval_callback = self._request_tool_approval
            command.delegation_callback = self._request_agent_delegation
            if payload.get("mcp_runtime_configs"):
                if self.mcp_manager is None:
                    raise TransientWorkerError("MCP Runtime Manager is unavailable")
                command.mcp_servers = self.mcp_manager.execution_configs(
                    payload["mcp_runtime_configs"]
                )
            release_binding = payload.get("release_binding")
            if release_binding:
                if self.release_store is None:
                    raise TransientWorkerError("Agent Release store is unavailable")
                release_path = self.release_store.verify_installed(
                    release_binding["agent_id"],
                    release_binding["release_id"],
                    release_binding["resolved_spec_digest"],
                )
                command.add_dirs = [str(release_path)]
                command.skills = list(release_binding.get("skill_slugs", []))
        except (KeyError, TypeError, ValueError) as exc:
            raise TransientWorkerError("claim response is invalid") from exc

        cancelled = asyncio.Event()
        ownership_lost = asyncio.Event()
        timed_out = asyncio.Event()
        lease = asyncio.create_task(
            self._renew_lease(task_id, revision, cancelled, ownership_lost)
        )
        last_sequence = command.start_sequence
        try:
            try:

                async def execute() -> None:
                    nonlocal last_sequence
                    async for event in self.shell.run_session(command, task_id=task_id):
                        if ownership_lost.is_set():
                            return
                        last_sequence = int(event["sequence"])
                        if (cancelled.is_set() or timed_out.is_set()) and event[
                            "event_type"
                        ] == "result":
                            event = {
                                "sequence": last_sequence,
                                "event_type": "status",
                                "payload": {
                                    "state": "sdk_interrupted",
                                    "sdk_terminal": event["payload"],
                                },
                            }
                        await self._post_event(task_id, event)

                execution = asyncio.create_task(execute())
                done, _ = await asyncio.wait(
                    {execution}, timeout=command.timeout_seconds
                )
                if not done:
                    timed_out.set()
                    await self.shell.interrupt(task_id)
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(execution), timeout=self.interrupt_grace
                        )
                    except TimeoutError:
                        execution.cancel()
                        await asyncio.gather(execution, return_exceptions=True)
                    last_sequence += 1
                    await self._post_event(
                        task_id,
                        {
                            "sequence": last_sequence,
                            "event_type": "error",
                            "payload": {
                                "code": "task_timeout",
                                "timeout_seconds": command.timeout_seconds,
                            },
                        },
                    )
                else:
                    execution.result()
            except TaskOwnershipLost:
                ownership_lost.set()
                await self.shell.interrupt(task_id)
                return True
            except (PermanentWorkerError, TransientWorkerError):
                await self.shell.interrupt(task_id)
                raise
            except Exception as exc:
                logger.exception(
                    "runtime SDK execution failed", extra={"task_id": task_id}
                )
                last_sequence += 1
                try:
                    await self._post_event(
                        task_id,
                        {
                            "sequence": last_sequence,
                            "event_type": "error",
                            "payload": {
                                "code": "executor_error",
                                "message": str(exc),
                            },
                        },
                    )
                except TaskOwnershipLost:
                    ownership_lost.set()
                    await self.shell.interrupt(task_id)
                return True

            if cancelled.is_set() and not ownership_lost.is_set():
                await self._post_event(
                    task_id,
                    {
                        "sequence": last_sequence + 1,
                        "event_type": "status",
                        "payload": {"state": "cancelled"},
                    },
                )
        finally:
            lease.cancel()
            try:
                await lease
            except asyncio.CancelledError:
                pass
            except (TaskOwnershipLost, TransientWorkerError):
                ownership_lost.set()
            except PermanentWorkerError:
                raise
        return True

    async def _renew_lease(
        self,
        task_id: str,
        revision: int,
        cancelled: asyncio.Event,
        ownership_lost: asyncio.Event,
    ) -> None:
        failures = 0
        while True:
            await asyncio.sleep(self.lease_interval)
            try:
                response = await self.client.post(
                    f"/api/v1/internal/runtime/tasks/{task_id}/lease",
                    headers=self.headers,
                    json={"worker_id": self.worker_id, "revision": revision},
                )
                self._classify_response(response, task_scoped=True)
                failures = 0
            except TaskOwnershipLost:
                ownership_lost.set()
                await self.shell.interrupt(task_id)
                raise
            except PermanentWorkerError:
                ownership_lost.set()
                await self.shell.interrupt(task_id)
                raise
            except (httpx.TransportError, TransientWorkerError) as exc:
                failures += 1
                if failures < 3:
                    continue
                ownership_lost.set()
                await self.shell.interrupt(task_id)
                raise TransientWorkerError("lease renewal retries exhausted") from exc
            try:
                status = response.json()["status"]
            except (KeyError, TypeError, ValueError) as exc:
                ownership_lost.set()
                await self.shell.interrupt(task_id)
                raise TransientWorkerError("lease response is invalid") from exc
            if status == "cancelling":
                cancelled.set()
                await self.shell.interrupt(task_id)
                return

    async def run_forever(self) -> None:
        attempt = 0
        while True:
            try:
                if (
                    self.harness_capabilities is not None
                    and time.monotonic() - self._last_capability_report
                    >= self.capability_report_interval
                ):
                    await self.report_capabilities()
                validated_mcp = await self.validate_mcp_once()
                applied = await self.apply_release_once()
                ran_runtime_job = await self.run_runtime_job_once()
                worked = await self.run_once()
                attempt = 0
                if (
                    not worked
                    and not applied
                    and not validated_mcp
                    and not ran_runtime_job
                ):
                    await asyncio.sleep(2)
            except PermanentWorkerError:
                logger.critical(
                    "runtime worker stopped due to permanent configuration error"
                )
                raise
            except (TransientWorkerError, httpx.TransportError) as exc:
                delay = min(self.max_backoff, 2 ** min(attempt, 6))
                delay *= random.uniform(0.8, 1.0)
                logger.warning(
                    "runtime worker will retry after recoverable failure",
                    extra={"attempt": attempt, "delay": delay, "error": str(exc)},
                )
                await asyncio.sleep(delay)
                attempt += 1
            except Exception:
                delay = min(self.max_backoff, 2 ** min(attempt, 6))
                logger.exception(
                    "runtime worker supervisor caught an unexpected failure",
                    extra={"attempt": attempt, "delay": delay},
                )
                await asyncio.sleep(delay)
                attempt += 1
