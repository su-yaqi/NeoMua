import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.db import engine
from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    AgentTask,
    RuntimeNode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from app.runtime.policy import TaskStatus
from app.runtime.repository import (
    append_and_apply_event,
    expire_dispatch_reservations,
    reserve_node_tasks,
)
from tests.api.routes.test_namespaces import create_namespace


def _runtime_and_node(db: Session) -> tuple[RuntimeProfile, RuntimeNode]:
    namespace = create_namespace(db)
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type=RuntimeType.NODE,
        route_mode=RuntimeRouteMode.DIRECT_ANTHROPIC,
        model_id="test",
    )
    node = RuntimeNode(
        namespace_id=namespace.id,
        name="node",
        hostname="node",
        os_name="linux",
        architecture="arm64",
        agent_version="1",
        public_key="key",
        key_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    db.add(runtime)
    db.add(node)
    db.commit()
    return runtime, node


def test_same_event_is_atomic_across_two_database_sessions(db: Session) -> None:
    runtime, _ = _runtime_and_node(db)
    task = AgentTask(
        namespace_id=runtime.namespace_id,
        runtime_profile_id=runtime.id,
        prompt="work",
        status=TaskStatus.DISPATCHED,
    )
    db.add(task)
    db.commit()

    def append() -> bool:
        with Session(engine) as independent:
            result = append_and_apply_event(
                independent,
                task.id,
                1,
                AgentEventType.STATUS,
                {"state": "running"},
            )
            independent.commit()
            return result.duplicate

    with ThreadPoolExecutor(max_workers=2) as executor:
        duplicates = list(executor.map(lambda _: append(), range(2)))
    db.expire_all()
    events = db.exec(select(AgentEvent).where(AgentEvent.task_id == task.id)).all()
    assert len(events) == 1
    assert sorted(duplicates) == [False, True]


def test_only_one_connection_can_reserve_then_expiry_releases(db: Session) -> None:
    runtime, node = _runtime_and_node(db)
    task = AgentTask(
        namespace_id=runtime.namespace_id,
        runtime_profile_id=runtime.id,
        target_node_id=node.id,
        prompt="work",
    )
    db.add(task)
    db.commit()
    first_connection = uuid.uuid4()
    second_connection = uuid.uuid4()
    with Session(engine) as first:
        assert [
            item.id
            for item in reserve_node_tasks(
                first,
                node_id=node.id,
                connection_id=first_connection,
                ttl_seconds=30,
            )
        ] == [task.id]
    with Session(engine) as second:
        assert (
            reserve_node_tasks(second, node_id=node.id, connection_id=second_connection)
            == []
        )
        expire_dispatch_reservations(
            second, now=datetime.now(timezone.utc) + timedelta(seconds=31)
        )
        second.commit()
        assert [
            item.id
            for item in reserve_node_tasks(
                second, node_id=node.id, connection_id=second_connection
            )
        ] == [task.id]
