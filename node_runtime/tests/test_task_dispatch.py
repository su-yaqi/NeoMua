import asyncio

import pytest
from node_runtime.model_route import ModelRouteStore
from node_runtime.protocol import envelope
from node_runtime.spool import EventSpool
from node_runtime.tasks import DispatchDecision, NodeTaskController, TaskDispatcher


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
