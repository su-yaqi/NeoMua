import asyncio
import json

import httpx
import pytest

from runtime_worker.worker import RuntimeWorker


class FakeShell:
    async def run_session(self, command, *, task_id=None):
        yield {
            "sequence": command.start_sequence + 1,
            "event_type": "assistant_message",
            "payload": {"text": "hi"},
        }
        yield {
            "sequence": command.start_sequence + 2,
            "event_type": "result",
            "payload": {"result": "done"},
        }


class CancellableShell:
    def __init__(self) -> None:
        self.interrupted = asyncio.Event()

    async def run_session(self, command, *, task_id=None):
        await self.interrupted.wait()
        yield {
            "sequence": command.start_sequence + 1,
            "event_type": "result",
            "payload": {"result": "interrupted"},
        }

    async def interrupt(self, task_id):
        self.interrupted.set()
        return True


class HangingShell:
    def __init__(self) -> None:
        self.interrupted = False

    async def run_session(self, command, *, task_id=None):
        await asyncio.Event().wait()
        yield  # pragma: no cover

    async def interrupt(self, task_id):
        self.interrupted = True
        return True


@pytest.mark.anyio
async def test_worker_claims_executes_and_posts_events() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tasks/claim"):
            return httpx.Response(
                200,
                json={
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "revision": 1,
                    "command": {
                        "prompt": "hello",
                        "model": "claude",
                        "permission_mode": "default",
                        "tools": [],
                        "allowed_tools": [],
                        "disallowed_tools": [],
                        "cwd": None,
                        "env": {},
                        "sdk_session_id": None,
                        "start_sequence": 1,
                    },
                },
            )
        return httpx.Response(200, json={"accepted": 2})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://control"
    ) as client:
        worker = RuntimeWorker(client, "token", "worker-1", shell=FakeShell())
        assert await worker.run_once()
    event_requests = requests[1:]
    assert [
        json.loads(item.content)["events"][0]["sequence"] for item in event_requests
    ] == [2, 3]


@pytest.mark.anyio
async def test_worker_interrupts_and_posts_cancelled_terminal_event() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tasks/claim"):
            return httpx.Response(
                200,
                json={
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "revision": 1,
                    "command": {
                        "prompt": "hello",
                        "model": "claude",
                        "permission_mode": "default",
                        "tools": [],
                        "allowed_tools": [],
                        "disallowed_tools": [],
                        "cwd": None,
                        "env": {},
                        "sdk_session_id": None,
                        "start_sequence": 1,
                    },
                },
            )
        if request.url.path.endswith("/lease"):
            return httpx.Response(200, json={"status": "cancelling"})
        return httpx.Response(200, json={"accepted": 1})

    shell = CancellableShell()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://control"
    ) as client:
        worker = RuntimeWorker(
            client, "token", "worker-1", shell=shell, lease_interval=0.01
        )
        assert await worker.run_once()
    event_payloads = [
        json.loads(request.content)
        for request in requests
        if request.url.path.endswith("/events")
    ]
    assert event_payloads[-1]["events"][0]["payload"]["state"] == "cancelled"


@pytest.mark.anyio
async def test_worker_timeout_interrupts_and_posts_structured_error() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tasks/claim"):
            return httpx.Response(
                200,
                json={
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "revision": 1,
                    "command": {
                        "prompt": "hello",
                        "model": "claude",
                        "permission_mode": "default",
                        "timeout_seconds": 1,
                    },
                },
            )
        return httpx.Response(200, json={"accepted": 1})

    shell = HangingShell()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://control"
    ) as client:
        worker = RuntimeWorker(
            client,
            "token",
            "worker-1",
            shell=shell,
            lease_interval=60,
            interrupt_grace=0.01,
        )
        assert await worker.run_once()
    assert shell.interrupted
    payload = next(
        json.loads(request.content)
        for request in requests
        if request.url.path.endswith("/events")
    )
    assert payload["events"][0]["payload"]["code"] == "task_timeout"
