from node_runtime.spool import EventSpool
from node_runtime.tasks import DispatchDecision, TaskDispatcher


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
    assert dispatcher.accept(
        {"task_id": "task-1", "revision": 1, "snapshot": {}}
    ) == DispatchDecision.STALE
