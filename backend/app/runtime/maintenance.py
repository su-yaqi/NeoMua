import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlmodel import Session, col, select

from app.conversation_management.models import Conversation, ConversationStatus
from app.conversation_management.service import reconcile_agent_messages
from app.core.db import engine
from app.runtime.jobs import (
    expire_runtime_job_leases,
    expire_runtime_job_reservations,
)
from app.runtime.models import (
    ArtifactDeployment,
    ArtifactRelease,
    DeploymentStatus,
    NodeCredential,
)
from app.runtime.repository import expire_dispatch_reservations, expire_task_leases
from app.workflow_management.engine import reconcile_instance
from app.workflow_management.models import WorkflowInstance, WorkflowInstanceStatus

logger = logging.getLogger(__name__)
_MAINTENANCE_LOCK_ID = 0x4E454F4D5541


def run_maintenance_once(*, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    with Session(engine) as session:
        acquired = (
            session.connection()
            .execute(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                {"lock_id": _MAINTENANCE_LOCK_ID},
            )
            .scalar_one()
        )
        if not acquired:
            session.rollback()
            return False
        expire_dispatch_reservations(session, now=current)
        expire_task_leases(session, now=current)
        expire_runtime_job_reservations(session, now=current)
        expire_runtime_job_leases(session, now=current)

        deployments = session.exec(
            select(ArtifactDeployment)
            .where(
                ArtifactDeployment.status == DeploymentStatus.PENDING,
                col(ArtifactDeployment.dispatch_reserved_until) <= current,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for deployment in deployments:
            deployment.dispatch_connection_id = None
            deployment.dispatch_reserved_until = None
            session.add(deployment)

        active_deployments = session.exec(
            select(ArtifactDeployment)
            .where(
                col(ArtifactDeployment.status).in_(
                    [DeploymentStatus.PENDING, DeploymentStatus.DISPATCHED]
                )
            )
            .with_for_update(skip_locked=True)
        ).all()
        for deployment in active_deployments:
            release = session.get(ArtifactRelease, deployment.release_id)
            if release is None or release.valid_until <= current:
                deployment.status = DeploymentStatus.EXPIRED
                deployment.error = {"code": "release_expired"}
                deployment.dispatch_connection_id = None
                deployment.dispatch_reserved_until = None
                session.add(deployment)

        credentials = session.exec(
            select(NodeCredential)
            .where(
                col(NodeCredential.replacement_grace_until) <= current,
                col(NodeCredential.revoked_at).is_(None),
                col(NodeCredential.replaced_by_id).is_not(None),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for credential in credentials:
            credential.revoked_at = current
            session.add(credential)

        conversations = session.exec(
            select(Conversation)
            .where(Conversation.status == ConversationStatus.ACTIVE)
            .order_by(col(Conversation.updated_at))
            .limit(200)
            .with_for_update(skip_locked=True)
        ).all()
        for conversation in conversations:
            reconcile_agent_messages(session, conversation)

        workflow_instances = session.exec(
            select(WorkflowInstance)
            .where(
                col(WorkflowInstance.status).in_(
                    [
                        WorkflowInstanceStatus.PENDING,
                        WorkflowInstanceStatus.RUNNING,
                        WorkflowInstanceStatus.WAITING,
                        WorkflowInstanceStatus.BLOCKED,
                    ]
                )
            )
            .order_by(col(WorkflowInstance.updated_at))
            .limit(200)
            .with_for_update(skip_locked=True)
        ).all()
        for instance in workflow_instances:
            reconcile_instance(session, instance)
        session.commit()
        return True


async def maintenance_loop(interval_seconds: float = 30) -> None:
    while True:
        try:
            await asyncio.to_thread(run_maintenance_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("runtime maintenance iteration failed")
        await asyncio.sleep(interval_seconds)
