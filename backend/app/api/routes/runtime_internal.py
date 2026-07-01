import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.runtime.models import AgentEvent, AgentEventType, AgentTask
from app.runtime.security import require_internal_runtime

router = APIRouter(
    prefix="/internal/runtime",
    tags=["runtime-internal"],
    dependencies=[Depends(require_internal_runtime)],
)


class EventInput(BaseModel):
    sequence: int
    event_type: AgentEventType
    payload: dict


class EventBatch(BaseModel):
    task_id: uuid.UUID
    events: list[EventInput]


@router.post("/events")
def append_events(body: EventBatch, session: SessionDep) -> dict[str, int]:
    task = session.get(AgentTask, body.task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    for item in body.events:
        event = AgentEvent(
            namespace_id=task.namespace_id,
            task_id=task.id,
            sequence=item.sequence,
            event_type=item.event_type,
            payload=item.payload,
        )
        session.add(event)
    session.commit()
    return {"accepted": len(body.events)}
