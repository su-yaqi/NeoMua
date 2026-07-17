import asyncio
import os
import socket
from pathlib import Path

import httpx

from runtime_worker.capabilities import discover_runtime_capabilities
from runtime_worker.mcp_manager import McpRuntimeManager
from runtime_worker.release_store import AgentReleaseStore
from runtime_worker.skill_store import SkillStore
from runtime_worker.worker import RuntimeWorker


async def main() -> None:
    control_url = os.environ["CONTROL_PLANE_URL"]
    token = os.environ.pop("INTERNAL_RUNTIME_TOKEN")
    async with httpx.AsyncClient(base_url=control_url, timeout=30) as client:
        concurrency = int(os.environ.get("RUNTIME_WORKER_CONCURRENCY", "4"))
        if concurrency < 2:
            raise RuntimeError(
                "RUNTIME_WORKER_CONCURRENCY must be at least 2 for roundtable delegation"
            )
        release_root = Path(
            os.environ.get("NEOMUA_AGENT_RELEASE_ROOT", "/var/lib/neomua/releases")
        )
        capabilities = discover_runtime_capabilities()
        workers = [
            RuntimeWorker(
                client,
                token,
                f"{socket.gethostname()}:{index + 1}",
                harness_capabilities=capabilities,
                release_store=AgentReleaseStore(release_root),
                skill_store=SkillStore(release_root / "skill-cache"),
                mcp_manager=McpRuntimeManager(),
            )
            for index in range(concurrency)
        ]
        await asyncio.gather(*(worker.run_forever() for worker in workers))


if __name__ == "__main__":
    asyncio.run(main())
