import asyncio

import pytest
from node_runtime.model_route import ModelRouteStore
from node_runtime.protocol import envelope
from node_runtime.spool import EventSpool
from node_runtime.tasks import DispatchDecision, NodeTaskController, TaskDispatcher
from runtime_worker.runtime_configuration import (
    RuntimeConfigurationStore,
    canonical_digest,
)


class FakeExecutor:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, task_id: str, revision: int, command: dict) -> None:
        self.started.append(task_id)


def test_duplicate_revision_starts_one_executor(tmp_path) -> None:
    executor = FakeExecutor()
    dispatcher = TaskDispatcher(EventSpool(tmp_path / "spool.db"), executor)
    command = {"task_id": "task-1", "revision": 1, "snapshot": {"model_id": "x"}}
    assert dispatcher.accept(command) == DispatchDecision.ACCEPTED
    assert dispatcher.accept(command) == DispatchDecision.DUPLICATE
    assert executor.started == ["task-1"]


def test_stale_revision_is_rejected(tmp_path) -> None:
    dispatcher = TaskDispatcher(EventSpool(tmp_path / "spool.db"), FakeExecutor())
    dispatcher.accept({"task_id": "task-1", "revision": 2, "snapshot": {}})
    assert (
        dispatcher.accept({"task_id": "task-1", "revision": 1, "snapshot": {}})
        == DispatchDecision.STALE
    )


@pytest.mark.anyio
async def test_v09_dispatch_rejects_server_evidence_that_differs_from_local_state(
    tmp_path,
) -> None:
    store = RuntimeConfigurationStore(tmp_path / "runtime-configurations.json")
    material = {
        "executable": "claude",
        "arguments": [],
        "working_directory_policy": "workspace",
        "environment_allowlist": [],
        "security_policy": {"permission_modes": ["default"]},
        "resource_limits": {},
    }
    applied = store.apply(
        {
            "runtime_instance_id": "runtime-1",
            "configuration_revision_id": "configuration-1",
            "configuration_digest": canonical_digest(material),
            "engine_type": "claude_code",
            **material,
        },
        engine_version="2.1.191",
        adapter_version="0.1.0",
        capabilities={
            "tools": [],
            "permission_modes": ["default"],
            "supports_per_tool_approval": False,
            "supports_mcp_injection": False,
        },
        discovered_models=[{"id": "claude-test"}],
    )
    store.record_validated_model(
        applied.runtime_instance_id, "claude-test", "native"
    )
    controller = NodeTaskController(
        "00000000-0000-0000-0000-000000000010",
        EventSpool(tmp_path / "spool.db"),
        ModelRouteStore(tmp_path / "routes.json"),
        runtime_configuration_store=store,
    )
    response = await controller.handle(
        envelope(
            "task_dispatch",
            "00000000-0000-0000-0000-000000000010",
            {
                "task_id": "task-local-evidence",
                "revision": 1,
                "requires_model_preparation": True,
                "snapshot": {
                    "runtime_instance_id": "runtime-1",
                    "runtime_model_binding_id": "binding-1",
                    "engine_type": "claude_code",
                    "engine_version": "2.1.191",
                    "adapter_version": "0.1.0",
                    "runtime_configuration_digest": applied.configuration_digest,
                    "capability_fingerprint": "server-spoofed-fingerprint",
                    "permission_mode": "default",
                    "timeout_seconds": 60,
                    "allowed_tools": [],
                    "route_type": "runtime_native",
                    "route_key": "native",
                    "engine_model_id": "claude-test",
                    "effective_spec_digest": "effective-1",
                },
            },
        )
    )
    assert response[0].type == "task_rejected"
    assert response[0].payload["reason"] == "runtime_capability_fingerprint_mismatch"


@pytest.mark.anyio
async def test_duplicate_runtime_job_replays_result_without_execution(
    tmp_path, monkeypatch
) -> None:
    executions: list[tuple[str, dict]] = []

    def execute(kind: str, payload: dict) -> dict:
        executions.append((kind, payload))
        return {"proof": "done"}

    monkeypatch.setattr("node_runtime.tasks.execute_runtime_job", execute)
    spool = EventSpool(tmp_path / "spool.db")
    controller = NodeTaskController(
        "00000000-0000-0000-0000-000000000010",
        spool,
        ModelRouteStore(tmp_path / "routes.json"),
    )
    dispatch = envelope(
        "runtime_job_dispatch",
        "00000000-0000-0000-0000-000000000010",
        {
            "job_id": "job-1",
            "revision": 1,
            "kind": "repository_probe",
            "payload": {"repository_id": "repo-1"},
            "side_effecting": True,
        },
    )
    accepted = await controller.handle(dispatch)
    assert accepted[0].type == "runtime_job_accepted"
    result = await asyncio.wait_for(controller.outbox.get(), timeout=1)
    assert result.type == "runtime_job_result"

    duplicate = await controller.handle(dispatch)
    assert [message.type for message in duplicate] == [
        "runtime_job_accepted",
        "runtime_job_result",
    ]
    assert executions == [("repository_probe", {"repository_id": "repo-1"})]
