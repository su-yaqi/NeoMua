import asyncio
import logging
import random
from typing import Any

import httpx

from runtime_worker.agent_shell import AgentShell, RunCommand

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
    ) -> None:
        self.client = client
        self.headers = {"X-Runtime-Token": internal_token}
        self.worker_id = worker_id
        self.shell = shell or AgentShell()
        self.lease_interval = lease_interval
        self.max_backoff = max_backoff
        self.interrupt_grace = interrupt_grace

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
                        if (
                            cancelled.is_set() or timed_out.is_set()
                        ) and event["event_type"] == "result":
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
                raise TransientWorkerError(
                    "lease renewal retries exhausted"
                ) from exc
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
                worked = await self.run_once()
                attempt = 0
                if not worked:
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
