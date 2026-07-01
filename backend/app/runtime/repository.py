import json

from sqlmodel import Session, select

from app.runtime.models import AgentEvent, AgentEventType, AgentTask


def append_event_idempotent(
    session: Session,
    task: AgentTask,
    sequence: int,
    event_type: AgentEventType,
    payload: dict,
) -> AgentEvent:
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
