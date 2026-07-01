import json

import httpx
import pytest

from runtime_worker.worker import RuntimeWorker


class FakeShell:
    async def run_session(self, command):
        yield {"sequence": command.start_sequence + 1, "event_type": "assistant_message", "payload": {"text": "hi"}}
        yield {"sequence": command.start_sequence + 2, "event_type": "result", "payload": {"result": "done"}}


@pytest.mark.anyio
async def test_worker_claims_executes_and_posts_events() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tasks/claim"):
            return httpx.Response(200, json={"task_id": "00000000-0000-0000-0000-000000000001", "revision": 1,
                "command": {"prompt": "hello", "model": "claude", "permission_mode": "default", "tools": [],
                "allowed_tools": [], "disallowed_tools": [], "cwd": None, "env": {}, "sdk_session_id": None,
                "start_sequence": 1}})
        return httpx.Response(200, json={"accepted": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://control") as client:
        worker = RuntimeWorker(client, "token", "worker-1", shell=FakeShell())
        assert await worker.run_once()
    event_requests = requests[1:]
    assert [json.loads(item.content)["events"][0]["sequence"] for item in event_requests] == [2, 3]
