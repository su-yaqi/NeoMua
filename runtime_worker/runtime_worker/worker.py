import asyncio
from typing import Any

import httpx

from runtime_worker.agent_shell import AgentShell, RunCommand


class RuntimeWorker:
    def __init__(
        self,
        client: httpx.AsyncClient,
        internal_token: str,
        worker_id: str,
        *,
        shell: Any | None = None,
        lease_interval: float = 60,
    ) -> None:
        self.client = client
        self.headers = {"X-Runtime-Token": internal_token}
        self.worker_id = worker_id
        self.shell = shell or AgentShell()
        self.lease_interval = lease_interval

    async def run_once(self) -> bool:
        response = await self.client.post(
            "/api/v1/internal/runtime/tasks/claim",
            headers=self.headers,
            json={"worker_id": self.worker_id},
        )
        if response.status_code == 204:
            return False
        response.raise_for_status()
        payload = response.json()
        command = RunCommand(**payload["command"])
        cancelled = asyncio.Event()
        lease = asyncio.create_task(
            self._renew_lease(payload["task_id"], payload["revision"], cancelled)
        )
        last_sequence = command.start_sequence
        try:
            async for event in self.shell.run_session(
                command, task_id=payload["task_id"]
            ):
                last_sequence = int(event["sequence"])
                if cancelled.is_set() and event["event_type"] == "result":
                    event = {
                        "sequence": last_sequence,
                        "event_type": "status",
                        "payload": {
                            "state": "sdk_interrupted",
                            "sdk_terminal": event["payload"],
                        },
                    }
                posted = await self.client.post(
                    "/api/v1/internal/runtime/events",
                    headers=self.headers,
                    json={"task_id": payload["task_id"], "events": [event]},
                )
                posted.raise_for_status()
            if cancelled.is_set():
                posted = await self.client.post(
                    "/api/v1/internal/runtime/events",
                    headers=self.headers,
                    json={
                        "task_id": payload["task_id"],
                        "events": [
                            {
                                "sequence": last_sequence + 1,
                                "event_type": "status",
                                "payload": {"state": "cancelled"},
                            }
                        ],
                    },
                )
                posted.raise_for_status()
        finally:
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
        return True

    async def _renew_lease(
        self, task_id: str, revision: int, cancelled: asyncio.Event
    ) -> None:
        while True:
            await asyncio.sleep(self.lease_interval)
            response = await self.client.post(
                f"/api/v1/internal/runtime/tasks/{task_id}/lease",
                headers=self.headers,
                json={"worker_id": self.worker_id, "revision": revision},
            )
            response.raise_for_status()
            if response.json()["status"] == "cancelling":
                cancelled.set()
                await self.shell.interrupt(task_id)
                return

    async def run_forever(self) -> None:
        while True:
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(2)
