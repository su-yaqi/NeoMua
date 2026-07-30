from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.runtime.models import AgentTask, RuntimeProfile, RuntimeRouteMode, RuntimeType
from app.runtime.policy import TaskStatus
from app.runtime.repository import expire_task_leases, retry_task
from tests.api.routes.test_namespaces import create_namespace


def _task(db: Session, status: TaskStatus) -> AgentTask:
    namespace = create_namespace(db)
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type=RuntimeType.PLATFORM,
        route_mode=RuntimeRouteMode.DIRECT_ANTHROPIC,
        model_id="claude",
    )
    db.add(runtime)
    db.flush()
    task = AgentTask(
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        prompt="work",
        status=status,
        snapshot={"model_id": "claude", "runtime_revision": 1},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_retry_creates_new_task_linked_to_original(db: Session) -> None:
    original = _task(db, TaskStatus.FAILED)
    retried = retry_task(db, original.id)
    assert retried.id != original.id
    assert retried.retry_of_task_id == original.id
    assert retried.status == TaskStatus.QUEUED
    assert retried.snapshot == original.snapshot


def test_expired_running_lease_becomes_interrupted(db: Session) -> None:
    task = _task(db, TaskStatus.RUNNING)
    now = datetime.now(timezone.utc)
    task.lease_expires_at = now - timedelta(seconds=1)
    db.add(task)
    db.commit()
    assert expire_task_leases(db, now=now) == 1
    db.refresh(task)
    assert task.status == TaskStatus.INTERRUPTED
