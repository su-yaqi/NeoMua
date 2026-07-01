import pytest
from sqlmodel import Session

from app.runtime.models import (
    AgentEventType,
    AgentTask,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from app.runtime.repository import append_event_idempotent
from tests.api.routes.test_namespaces import create_namespace
from tests.utils.user import create_random_user


@pytest.fixture
def runtime_task_factory(db: Session):
    created: list[AgentTask] = []

    def factory() -> AgentTask:
        namespace = create_namespace(db)
        user = create_random_user(db)
        runtime = RuntimeProfile(
            namespace_id=namespace.id, runtime_type=RuntimeType.PLATFORM,
            route_mode=RuntimeRouteMode.DIRECT_ANTHROPIC, model_id="test",
        )
        db.add(runtime)
        db.flush()
        task = AgentTask(
            namespace_id=namespace.id, runtime_profile_id=runtime.id,
            prompt="test", created_by=user.id,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        created.append(task)
        return task

    return factory


def test_append_event_is_idempotent(db: Session, runtime_task_factory) -> None:
    task = runtime_task_factory()
    first = append_event_idempotent(db, task, 1, AgentEventType.STATUS, {"state": "running"})
    second = append_event_idempotent(db, task, 1, AgentEventType.STATUS, {"state": "running"})
    assert first.id == second.id


def test_conflicting_event_sequence_is_rejected(db: Session, runtime_task_factory) -> None:
    task = runtime_task_factory()
    append_event_idempotent(db, task, 1, AgentEventType.STATUS, {"state": "running"})
    try:
        append_event_idempotent(db, task, 1, AgentEventType.ERROR, {"message": "bad"})
    except ValueError as exc:
        assert "conflict" in str(exc)
    else:
        raise AssertionError("conflicting event was accepted")
