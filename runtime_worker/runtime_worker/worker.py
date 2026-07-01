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
    ) -> None:
        self.client = client
        self.headers = {"X-Runtime-Token": internal_token}
        self.worker_id = worker_id
        self.shell = shell or AgentShell()

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
        async for event in self.shell.run_session(command):
            posted = await self.client.post(
                "/api/v1/internal/runtime/events",
                headers=self.headers,
                json={"task_id": payload["task_id"], "events": [event]},
            )
            posted.raise_for_status()
        return True

    async def run_forever(self) -> None:
        while True:
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(2)
