import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.runtime.models import (
    RuntimeJob,
    RuntimeJobKind,
    RuntimeJobStatus,
    RuntimeNode,
    RuntimeProfile,
    RuntimeType,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def runtime_target_node(session: Session, runtime: RuntimeProfile) -> uuid.UUID | None:
    if runtime.runtime_type == RuntimeType.PLATFORM:
        return None
    node = session.exec(
        select(RuntimeNode).where(
            RuntimeNode.runtime_profile_id == runtime.id,
            col(RuntimeNode.revoked_at).is_(None),
        )
    ).first()
    if node is None:
        raise HTTPException(409, "Target node Runtime is unavailable")
    return node.id


def enqueue_runtime_job(
    session: Session,
    *,
    namespace_id: uuid.UUID,
    runtime_id: uuid.UUID,
    kind: RuntimeJobKind,
    payload: dict[str, Any],
    idempotency_key: str,
    side_effecting: bool = False,
) -> RuntimeJob:
    existing = session.exec(
        select(RuntimeJob).where(
            RuntimeJob.namespace_id == namespace_id,
            RuntimeJob.idempotency_key == idempotency_key,
        )
    ).first()
    if existing is not None:
        return existing
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(409, "Runtime is unavailable")
    job = RuntimeJob(
        namespace_id=namespace_id,
        runtime_profile_id=runtime.id,
        target_node_id=runtime_target_node(session, runtime),
        kind=kind,
        payload=payload,
        side_effecting=side_effecting,
        idempotency_key=idempotency_key,
    )
    session.add(job)
    session.flush()
    return job


def reserve_node_runtime_jobs(
    session: Session,
    *,
    node_id: uuid.UUID,
    connection_id: uuid.UUID,
    limit: int = 10,
    now: datetime | None = None,
) -> list[RuntimeJob]:
    current = now or utcnow()
    jobs = session.exec(
        select(RuntimeJob)
        .where(
            RuntimeJob.target_node_id == node_id,
            RuntimeJob.status == RuntimeJobStatus.QUEUED,
            (
                col(RuntimeJob.dispatch_reserved_until).is_(None)
                | (col(RuntimeJob.dispatch_reserved_until) <= current)
            ),
        )
        .order_by(col(RuntimeJob.created_at))
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    reserved_until = current + timedelta(seconds=30)
    for job in jobs:
        job.dispatch_connection_id = connection_id
        job.dispatch_reserved_until = reserved_until
        session.add(job)
    session.commit()
    return list(jobs)


def expire_runtime_job_leases(session: Session, *, now: datetime | None = None) -> int:
    current = now or utcnow()
    jobs = session.exec(
        select(RuntimeJob)
        .where(
            col(RuntimeJob.status).in_(
                [RuntimeJobStatus.DISPATCHED, RuntimeJobStatus.RUNNING]
            ),
            col(RuntimeJob.lease_expires_at) < current,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        if job.side_effecting:
            job.status = RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION
            job.error = {"code": "runtime_job_lease_expired_external_state_unknown"}
            job.completed_at = current
        else:
            job.status = RuntimeJobStatus.QUEUED
            job.revision += 1
        job.claimed_by = None
        job.lease_expires_at = None
        job.dispatch_connection_id = None
        job.dispatch_reserved_until = None
        job.updated_at = current
        session.add(job)
    return len(jobs)


def expire_runtime_job_reservations(
    session: Session, *, now: datetime | None = None
) -> int:
    current = now or utcnow()
    jobs = session.exec(
        select(RuntimeJob)
        .where(
            RuntimeJob.status == RuntimeJobStatus.QUEUED,
            col(RuntimeJob.dispatch_reserved_until) <= current,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        job.dispatch_connection_id = None
        job.dispatch_reserved_until = None
        session.add(job)
    return len(jobs)
