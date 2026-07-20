import asyncio
import os
import socket
from pathlib import Path

import httpx

from runtime_worker.capabilities import discover_runtime_capabilities
from runtime_worker.mcp_manager import McpRuntimeManager
from runtime_worker.release_store import AgentReleaseStore
from runtime_worker.runtime_configuration import (
    RuntimeConfigurationStore,
    sanitize_process_environment,
)
from runtime_worker.skill_store import SkillStore
from runtime_worker.worker import RuntimeWorker


def _consume_release_digest(source_environment: dict[str, str]) -> str:
    release_digest = source_environment.pop("RUNTIME_WORKER_RELEASE_DIGEST", None)
    if (
        release_digest is None
        or len(release_digest) != 64
        or any(
            character not in "0123456789abcdef" for character in release_digest.lower()
        )
    ):
        raise RuntimeError(
            "RUNTIME_WORKER_RELEASE_DIGEST must be a 64-character hex digest"
        )
    return release_digest.lower()


async def main() -> None:
    control_url = os.environ["CONTROL_PLANE_URL"]
    token = os.environ.pop("INTERNAL_RUNTIME_TOKEN")
    concurrency = int(os.environ.get("RUNTIME_WORKER_CONCURRENCY", "4"))
    release_root = Path(
        os.environ.get("NEOMUA_AGENT_RELEASE_ROOT", "/var/lib/neomua/releases")
    )
    configuration_store_path = Path(
        os.environ.get(
            "NEOMUA_RUNTIME_CONFIGURATION_STORE",
            "/var/lib/neomua/runtime-configurations.json",
        )
    )
    client = httpx.AsyncClient(base_url=control_url, timeout=30)
    source_environment = sanitize_process_environment()
    release_digest = _consume_release_digest(source_environment)
    async with client:
        if concurrency < 2:
            raise RuntimeError(
                "RUNTIME_WORKER_CONCURRENCY must be at least 2 for roundtable delegation"
            )
        capabilities = discover_runtime_capabilities(mode="platform")
        capabilities["release_digest"] = release_digest
        configuration_store = RuntimeConfigurationStore(
            configuration_store_path,
            source_environment=source_environment,
        )
        workers = [
            RuntimeWorker(
                client,
                token,
                f"{socket.gethostname()}:{index + 1}",
                harness_capabilities=capabilities,
                release_store=AgentReleaseStore(release_root),
                skill_store=SkillStore(release_root / "skill-cache"),
                mcp_manager=McpRuntimeManager(),
                runtime_configuration_store=configuration_store,
            )
            for index in range(concurrency)
        ]
        await asyncio.gather(*(worker.run_forever() for worker in workers))


if __name__ == "__main__":
    asyncio.run(main())
