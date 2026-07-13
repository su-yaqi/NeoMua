import asyncio
import os
import socket
from pathlib import Path

import httpx

from runtime_worker.worker import RuntimeWorker
from runtime_worker.capabilities import discover_harness_capabilities
from runtime_worker.release_store import AgentReleaseStore
from runtime_worker.mcp_manager import McpRuntimeManager


async def main() -> None:
    control_url = os.environ["CONTROL_PLANE_URL"]
    token = os.environ.pop("INTERNAL_RUNTIME_TOKEN")
    async with httpx.AsyncClient(base_url=control_url, timeout=30) as client:
        await RuntimeWorker(
            client,
            token,
            socket.gethostname(),
            harness_capabilities=discover_harness_capabilities(),
            release_store=AgentReleaseStore(
                Path(
                    os.environ.get(
                        "NEOMUA_AGENT_RELEASE_ROOT", "/var/lib/neomua/releases"
                    )
                )
            ),
            mcp_manager=McpRuntimeManager(),
        ).run_forever()


if __name__ == "__main__":
    asyncio.run(main())
