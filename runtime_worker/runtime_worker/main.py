import asyncio
import os
import socket

import httpx

from runtime_worker.worker import RuntimeWorker


async def main() -> None:
    control_url = os.environ["CONTROL_PLANE_URL"]
    token = os.environ["INTERNAL_RUNTIME_TOKEN"]
    async with httpx.AsyncClient(base_url=control_url, timeout=30) as client:
        await RuntimeWorker(client, token, socket.gethostname()).run_forever()


if __name__ == "__main__":
    asyncio.run(main())
