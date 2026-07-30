import asyncio
import json
from pathlib import Path

import httpx
import pytest
from runtime_worker.runtime_configuration import (
    RuntimeConfigurationStore,
    canonical_digest,
)
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
async def test_worker_applies_runtime_instance_configuration_and_reports_capability(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    configuration = {
        "executable": "claude",
        "arguments": [],
        "working_directory_policy": "workspace",
        "environment_allowlist": [],
        "security_policy": {"permission_modes": ["default", "plan"]},
        "resource_limits": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/configurations/claim"):
            return httpx.Response(
                200,
                json={
                    "runtime_instance_id": "00000000-0000-0000-0000-000000000010",
                    "configuration_revision_id": "00000000-0000-0000-0000-000000000011",
                    "configuration_digest": canonical_digest(configuration),
                    "engine_type": "claude_code",
                    **configuration,
                },
            )
        return httpx.Response(200, json={"status": "applied"})

    capabilities = {
        "claude_code": {
            "cli_version": "2.1.191",
            "adapter_version": "0.1.0",
            "builtin_tools": ["Read", "Bash"],
            "permission_modes": ["default", "plan"],
            "supports_tool_filters": True,
            "supports_per_tool_approval": True,
            "supports_mcp_injection": True,
        }
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://control"
    ) as client:
        worker = RuntimeWorker(
            client,
            "token",
            "worker-1",
            shell=FakeShell(),
            harness_capabilities=capabilities,
            runtime_configuration_store=RuntimeConfigurationStore(
                tmp_path / "runtime-configurations.json"
            ),
        )
        assert await worker.apply_configuration_once()
    result = json.loads(requests[-1].content)
    assert result["runtime_instance_id"] == "00000000-0000-0000-0000-000000000010"
    assert result["status"] == "applied"
    assert result["engine_version"] == "2.1.191"
    assert result["adapter_version"] == "0.1.0"
    assert result["capabilities"]["tools"] == ["Read", "Bash"]


@pytest.mark.anyio
async def test_worker_claims_executes_and_posts_events(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    store = RuntimeConfigurationStore(tmp_path / "runtime-configurations.json")
    configuration = {
        "runtime_instance_id": "00000000-0000-0000-0000-000000000010",
        "configuration_revision_id": "00000000-0000-0000-0000-000000000012",
        "engine_type": "claude_code",
        "executable": "claude",
        "arguments": [],
        "working_directory_policy": "workspace",
        "environment_allowlist": [],
        "security_policy": {"permission_modes": ["default"]},
        "resource_limits": {},
    }
    configuration["configuration_digest"] = canonical_digest(
        {
            key: configuration[key]
            for key in (
                "executable",
                "arguments",
                "working_directory_policy",
                "environment_allowlist",
                "security_policy",
                "resource_limits",
            )
        }
    )
    applied = store.apply(
        configuration,
        engine_version="2.1.191",
        adapter_version="0.1.0",
        capabilities={
            "tools": [],
            "permission_modes": ["default"],
            "supports_tool_filters": False,
            "supports_per_tool_approval": False,
            "supports_mcp_injection": False,
        },
        discovered_models=[{"id": "claude-test"}],
    )
    store.record_validated_model(applied.runtime_instance_id, "claude-test", "native")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tasks/claim"):
            return httpx.Response(
                200,
                json={
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "revision": 1,
                    "requires_model_preparation": True,
                    "model_preparation": {
                        "runtime_instance_id": "00000000-0000-0000-0000-000000000010",
                        "runtime_model_binding_id": "00000000-0000-0000-0000-000000000011",
                        "engine_type": "claude_code",
                        "engine_version": "2.1.191",
                        "adapter_version": "0.1.0",
                        "engine_model_id": "claude-test",
                        "route_type": "runtime_native",
                        "route_reference": "native",
                        "runtime_configuration_digest": applied.configuration_digest,
                        "capability_fingerprint": applied.capability_fingerprint,
                        "effective_spec_digest": "c" * 64,
                    },
                    "command": {
                        "engine_type": "claude_code",
                        "prompt": "hello",
                        "model": "claude-test",
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
        if request.url.path.endswith("/model-prepared"):
            return httpx.Response(
                200, json={"route_type": "runtime_native", "environment": {}}
            )
        return httpx.Response(200, json={"accepted": 2})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://control"
    ) as client:
        worker = RuntimeWorker(
            client,
            "token",
            "worker-1",
            shell=FakeShell(),
            harness_capabilities={
                "claude_code": {
                    "cli_version": "2.1.191",
                    "adapter_version": "0.1.0",
                }
            },
            runtime_configuration_store=store,
        )
        assert await worker.run_once()
    prepared_request = next(
        item for item in requests if item.url.path.endswith("/model-prepared")
    )
    assert json.loads(prepared_request.content)["runtime_model_binding_id"] == (
        "00000000-0000-0000-0000-000000000011"
    )
    event_requests = [item for item in requests if item.url.path.endswith("/events")]
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


@pytest.mark.anyio
async def test_worker_executes_runtime_job_and_reports_result(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/jobs/claim"):
            return httpx.Response(
                200,
                json={
                    "job_id": "00000000-0000-0000-0000-000000000099",
                    "revision": 3,
                    "kind": "workflow_handler",
                    "payload": {"component_key": "test"},
                    "side_effecting": False,
                },
            )
        return httpx.Response(200, json={"status": "succeeded"})

    monkeypatch.setattr(
        "runtime_worker.worker.execute_runtime_job",
        lambda kind, payload: {"output": {"kind": kind, **payload}},
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://control"
    ) as client:
        worker = RuntimeWorker(client, "token", "worker-1", shell=FakeShell())
        assert await worker.run_runtime_job_once()
    result_request = next(
        request for request in requests if request.url.path.endswith("/result")
    )
    assert json.loads(result_request.content) == {
        "worker_id": "worker-1",
        "revision": 3,
        "status": "succeeded",
        "result": {"output": {"kind": "workflow_handler", "component_key": "test"}},
    }
