import json
import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.runtime.models import AgentEvent, AgentEventType, AgentTask
from app.runtime.policy import (
    InvalidTaskTransition,
    TaskStatus,
    require_task_transition,
)
from app.runtime.security import redact_event_payload


def append_event_idempotent(
    session: Session,
    task: AgentTask,
    sequence: int,
    event_type: AgentEventType,
    payload: dict,
) -> AgentEvent:
    payload = redact_event_payload(payload)
    existing = session.exec(
        select(AgentEvent).where(
            AgentEvent.task_id == task.id, AgentEvent.sequence == sequence
        )
    ).first()
    if existing:
        same_payload = json.dumps(existing.payload, sort_keys=True) == json.dumps(
            payload, sort_keys=True
        )
        if existing.event_type != event_type or not same_payload:
            if task.status in {TaskStatus.DISPATCHED, TaskStatus.RUNNING}:
                if task.status == TaskStatus.DISPATCHED:
                    require_task_transition(task.status, TaskStatus.RUNNING)
                    task.status = TaskStatus.RUNNING
                require_task_transition(task.status, TaskStatus.FAILED)
                task.status = TaskStatus.FAILED
                task.final_result = {
                    "code": "event_sequence_conflict",
                    "sequence": sequence,
                }
                task.completed_at = datetime.now(timezone.utc)
                session.add(task)
                session.commit()
            raise ValueError("event sequence conflict")
        return existing
    event = AgentEvent(
        namespace_id=task.namespace_id,
        task_id=task.id,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def apply_event_state(
    task: AgentTask, event_type: AgentEventType, payload: dict
) -> None:
    def transition(target: TaskStatus) -> None:
        if task.status == target:
            return
        require_task_transition(task.status, target)
        task.status = target

    try:
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
        elif event_type in {AgentEventType.RESULT, AgentEventType.ERROR}:
            target = (
                TaskStatus.SUCCEEDED
                if event_type == AgentEventType.RESULT
                else TaskStatus.FAILED
            )
            if task.status == TaskStatus.DISPATCHED:
                transition(TaskStatus.RUNNING)
            if task.status != target:
                transition(target)
            task.final_result = payload
            task.completed_at = datetime.now(timezone.utc)
            task.lease_expires_at = None
        task.updated_at = datetime.now(timezone.utc)
    except InvalidTaskTransition as exc:
        raise ValueError(str(exc)) from exc


def last_contiguous_event_sequence(session: Session, task_id: uuid.UUID) -> int:
    sequences = session.exec(
        select(AgentEvent.sequence)
        .where(AgentEvent.task_id == task_id)
        .order_by(AgentEvent.sequence)
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
        target_node_id=original.target_node_id,
        task_kind=original.task_kind,
        prompt=original.prompt,
        snapshot=original.snapshot,
        retry_of_task_id=original.id,
        created_by=original.created_by,
        idempotency_key=idempotency_key,
    )
    session.add(retried)
    session.commit()
    session.refresh(retried)
    return retried


def expire_task_leases(session: Session, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    tasks = session.exec(
        select(AgentTask)
        .where(
            AgentTask.status.in_([TaskStatus.DISPATCHED, TaskStatus.RUNNING]),
            AgentTask.lease_expires_at < current,
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
    session.commit()
    return len(tasks)
