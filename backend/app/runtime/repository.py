import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, col, select

from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    AgentSession,
    AgentTask,
    AgentTaskModelCallUsage,
    AgentTaskModelUsage,
)
from app.runtime.policy import (
    InvalidTaskTransition,
    TaskStatus,
    require_task_transition,
)
from app.runtime.security import redact_event_payload


@dataclass(frozen=True)
class AppendEventResult:
    event: AgentEvent
    duplicate: bool


class EventSequenceConflict(ValueError):
    pass


def _payload_matches(
    event: AgentEvent, event_type: AgentEventType, payload: dict[str, Any]
) -> bool:
    return event.event_type == event_type and json.dumps(
        event.payload, sort_keys=True, separators=(",", ":")
    ) == json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _record_protocol_conflict(task: AgentTask, sequence: int) -> None:
    now = datetime.now(timezone.utc)
    diagnostic = {"code": "event_sequence_conflict", "sequence": sequence}
    if task.status in {
        TaskStatus.QUEUED,
        TaskStatus.DISPATCHED,
        TaskStatus.RUNNING,
        TaskStatus.CANCELLING,
    }:
        task.status = TaskStatus.FAILED
        task.completed_at = now
        task.lease_expires_at = None
    task.final_result = diagnostic
    task.updated_at = now


def apply_event_state(
    task: AgentTask, event_type: AgentEventType, payload: dict[str, Any]
) -> None:
    now = datetime.now(timezone.utc)

    def transition(target: TaskStatus) -> None:
        if task.status == target:
            return
        require_task_transition(task.status, target)
        task.status = target

    try:
        if task.status == TaskStatus.CANCELLED:
            if event_type == AgentEventType.RESULT:
                current = dict(task.final_result or {})
                current["sdk_terminal"] = payload
                task.final_result = current
            task.updated_at = now
            return
        if task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
            TaskStatus.REJECTED,
        }:
            task.updated_at = now
            return
        if (
            event_type
            in {
                AgentEventType.ASSISTANT_MESSAGE,
                AgentEventType.TOOL_CALL,
                AgentEventType.TOOL_RESULT,
            }
            and task.status == TaskStatus.DISPATCHED
        ):
            transition(TaskStatus.RUNNING)
        elif event_type == AgentEventType.STATUS:
            state = payload.get("state")
            if state in {status.value for status in TaskStatus}:
                transition(TaskStatus(state))
                if task.status in {
                    TaskStatus.CANCELLED,
                    TaskStatus.INTERRUPTED,
                    TaskStatus.REJECTED,
                }:
                    task.final_result = payload
                    task.completed_at = now
                    task.lease_expires_at = None
        elif event_type == AgentEventType.RESULT:
            if task.status == TaskStatus.CANCELLING:
                transition(TaskStatus.CANCELLED)
                task.final_result = {
                    "code": "cancelled",
                    "sdk_terminal": payload,
                }
            else:
                if task.status == TaskStatus.DISPATCHED:
                    transition(TaskStatus.RUNNING)
                transition(TaskStatus.SUCCEEDED)
                task.final_result = payload
            task.completed_at = now
            task.lease_expires_at = None
        elif event_type == AgentEventType.ERROR:
            if task.status == TaskStatus.DISPATCHED:
                transition(TaskStatus.RUNNING)
            transition(TaskStatus.FAILED)
            task.final_result = payload
            task.completed_at = now
            task.lease_expires_at = None
        task.updated_at = now
    except InvalidTaskTransition as exc:
        raise ValueError(str(exc)) from exc


def append_and_apply_event(
    session: Session,
    task_id: uuid.UUID,
    sequence: int,
    event_type: AgentEventType,
    payload: dict[str, Any],
) -> AppendEventResult:
    """Append one event and advance its task under one caller-owned transaction."""
    task = session.exec(
        select(AgentTask).where(AgentTask.id == task_id).with_for_update()
    ).first()
    if task is None:
        raise LookupError("task not found")
    redacted = redact_event_payload(payload)
    if (
        task.runtime_instance_id is not None
        and event_type != AgentEventType.USER_MESSAGE
    ):
        usage = session.exec(
            select(AgentTaskModelUsage).where(
                AgentTaskModelUsage.task_id == task.id
            )
        ).first()
        if usage is None:
            raise ValueError("v0.9 task model usage evidence is missing")
        redacted = {
            **redacted,
            "model_execution": {
                "model_usage_id": str(usage.id),
                "runtime_instance_id": str(usage.runtime_instance_id),
                "runtime_model_binding_id": str(usage.runtime_model_binding_id),
                "model_definition_id": str(usage.model_definition_id),
                "engine_type": usage.engine_type,
                "engine_version": usage.engine_version,
                "adapter_version": usage.adapter_version,
                "route_type": usage.route_type,
                "selection_source": usage.selection_source,
                "effective_spec_digest": usage.effective_spec_digest,
            },
        }
    event_table = cast(Any, AgentEvent).__table__
    statement = (
        pg_insert(event_table)
        .values(
            id=uuid.uuid4(),
            namespace_id=task.namespace_id,
            task_id=task.id,
            sequence=sequence,
            event_type=event_type.value,
            payload=redacted,
            created_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["task_id", "sequence"])
        .returning(event_table.c.id)
    )
    inserted_id = session.connection().execute(statement).scalar_one_or_none()
    if inserted_id is None:
        existing = session.exec(
            select(AgentEvent).where(
                AgentEvent.task_id == task.id, AgentEvent.sequence == sequence
            )
        ).one()
        if not _payload_matches(existing, event_type, redacted):
            _record_protocol_conflict(task, sequence)
            session.add(task)
            session.flush()
            raise EventSequenceConflict("event sequence conflict")
        return AppendEventResult(existing, duplicate=True)

    event = session.get(AgentEvent, inserted_id)
    assert event is not None
    usage_payload = redacted.get("usage")
    if (
        task.runtime_instance_id is not None
        and isinstance(usage_payload, dict)
        and usage_payload
    ):
        model_usage = session.exec(
            select(AgentTaskModelUsage).where(
                AgentTaskModelUsage.task_id == task.id
            )
        ).one()
        last_call = session.exec(
            select(AgentTaskModelCallUsage)
            .where(AgentTaskModelCallUsage.task_id == task.id)
            .order_by(col(AgentTaskModelCallUsage.call_sequence).desc())
        ).first()
        session.add(
            AgentTaskModelCallUsage(
                task_id=task.id,
                runtime_model_binding_id=model_usage.runtime_model_binding_id,
                call_sequence=(last_call.call_sequence if last_call else 0) + 1,
                event_sequence=sequence,
                usage=usage_payload,
                status=(
                    "failed" if event_type == AgentEventType.ERROR else "succeeded"
                ),
                error=redacted if event_type == AgentEventType.ERROR else None,
            )
        )
    apply_event_state(task, event_type, redacted)
    if (
        event_type == AgentEventType.RESULT
        and task.session_id
        and redacted.get("session_id")
    ):
        agent_session = session.exec(
            select(AgentSession)
            .where(AgentSession.id == task.session_id)
            .with_for_update()
        ).first()
        if agent_session is not None:
            agent_session.sdk_session_id = str(redacted["session_id"])
            session.add(agent_session)
    session.add(task)
    session.flush()
    return AppendEventResult(event, duplicate=False)


def append_event_idempotent(
    session: Session,
    task: AgentTask,
    sequence: int,
    event_type: AgentEventType,
    payload: dict[str, Any],
) -> AgentEvent:
    """Compatibility wrapper; the caller remains responsible for commit/rollback."""
    return append_and_apply_event(session, task.id, sequence, event_type, payload).event


def last_contiguous_event_sequence(session: Session, task_id: uuid.UUID) -> int:
    sequences = session.exec(
        select(AgentEvent.sequence)
        .where(AgentEvent.task_id == task_id)
        .order_by(col(AgentEvent.sequence))
    ).all()
    contiguous = -1
    for sequence in sequences:
        if sequence != contiguous + 1:
            break
        contiguous = sequence
    return contiguous


def retry_task(
    session: Session, task_id: uuid.UUID, *, idempotency_key: str | None = None
) -> AgentTask:
    original = session.exec(
        select(AgentTask).where(AgentTask.id == task_id).with_for_update()
    ).first()
    if original is None:
        raise ValueError("task not found")
    if original.status not in {
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
        TaskStatus.REJECTED,
        TaskStatus.CANCELLED,
    }:
        raise ValueError("only unsuccessful terminal tasks can be retried")
    retried = AgentTask(
        namespace_id=original.namespace_id,
        session_id=original.session_id,
        runtime_profile_id=original.runtime_profile_id,
        runtime_instance_id=original.runtime_instance_id,
        target_node_id=original.target_node_id,
        task_kind=original.task_kind,
        prompt=original.prompt,
        snapshot=original.snapshot,
        agent_release_id=original.agent_release_id,
        runtime_agent_release_id=original.runtime_agent_release_id,
        resolved_spec_digest=original.resolved_spec_digest,
        retry_of_task_id=original.id,
        created_by=original.created_by,
        idempotency_key=idempotency_key,
    )
    session.add(retried)
    session.flush()
    append_and_apply_event(
        session,
        retried.id,
        0,
        AgentEventType.USER_MESSAGE,
        {"text": retried.prompt},
    )
    return retried


def expire_task_leases(session: Session, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    tasks = session.exec(
        select(AgentTask)
        .where(
            col(AgentTask.status).in_([TaskStatus.DISPATCHED, TaskStatus.RUNNING]),
            col(AgentTask.lease_expires_at) < current,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for task in tasks:
        require_task_transition(task.status, TaskStatus.INTERRUPTED)
        task.status = TaskStatus.INTERRUPTED
        task.completed_at = current
        task.updated_at = current
        task.lease_expires_at = None
        session.add(task)
    session.flush()
    return len(tasks)


def reserve_node_tasks(
    session: Session,
    *,
    node_id: uuid.UUID,
    connection_id: uuid.UUID,
    limit: int = 10,
    ttl_seconds: int = 30,
    now: datetime | None = None,
) -> list[AgentTask]:
    current = now or datetime.now(timezone.utc)
    tasks = session.exec(
        select(AgentTask)
        .where(
            AgentTask.target_node_id == node_id,
            AgentTask.status == TaskStatus.QUEUED,
            (
                col(AgentTask.dispatch_reserved_until).is_(None)
                | (col(AgentTask.dispatch_reserved_until) <= current)
            ),
        )
        .order_by(col(AgentTask.created_at))
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    reserved_until = current + timedelta(seconds=ttl_seconds)
    for task in tasks:
        task.dispatch_connection_id = connection_id
        task.dispatch_reserved_until = reserved_until
        session.add(task)
    session.commit()
    return list(tasks)


def release_task_reservation(
    session: Session, task_id: uuid.UUID, connection_id: uuid.UUID
) -> None:
    task = session.exec(
        select(AgentTask).where(AgentTask.id == task_id).with_for_update()
    ).first()
    if task and task.dispatch_connection_id == connection_id:
        task.dispatch_connection_id = None
        task.dispatch_reserved_until = None
        session.add(task)
        session.commit()


def expire_dispatch_reservations(
    session: Session, *, now: datetime | None = None
) -> int:
    current = now or datetime.now(timezone.utc)
    tasks = session.exec(
        select(AgentTask)
        .where(
            AgentTask.status == TaskStatus.QUEUED,
            col(AgentTask.dispatch_reserved_until) <= current,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for task in tasks:
        task.dispatch_connection_id = None
        task.dispatch_reserved_until = None
        session.add(task)
    session.flush()
    return len(tasks)
