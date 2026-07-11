import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, col, select

from app.runtime.models import (
    ArtifactDeployment,
    ArtifactRelease,
    DeploymentStatus,
)


class ArtifactReleaseService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def pending_for_node(
        self, node_id: uuid.UUID, *, now: datetime | None = None
    ) -> list[ArtifactDeployment]:
        current = now or datetime.now(timezone.utc)
        deployments = self.session.exec(
            select(ArtifactDeployment)
            .where(
                ArtifactDeployment.node_id == node_id,
                ArtifactDeployment.status == DeploymentStatus.PENDING,
            )
            .order_by(col(ArtifactDeployment.created_at))
            .with_for_update(skip_locked=True)
        ).all()
        pending: list[ArtifactDeployment] = []
        for deployment in deployments:
            release = self.session.get(ArtifactRelease, deployment.release_id)
            if release is None or release.valid_until <= current:
                deployment.status = DeploymentStatus.EXPIRED
                deployment.error = {"code": "release_expired_before_dispatch"}
                self.session.add(deployment)
            else:
                pending.append(deployment)
        self.session.commit()
        return pending

    def reserve_pending_for_node(
        self,
        node_id: uuid.UUID,
        connection_id: uuid.UUID,
        *,
        now: datetime | None = None,
        ttl_seconds: int = 30,
    ) -> list[ArtifactDeployment]:
        current = now or datetime.now(timezone.utc)
        deployments = self.session.exec(
            select(ArtifactDeployment)
            .where(
                ArtifactDeployment.node_id == node_id,
                ArtifactDeployment.status == DeploymentStatus.PENDING,
                (
                    col(ArtifactDeployment.dispatch_reserved_until).is_(None)
                    | (col(ArtifactDeployment.dispatch_reserved_until) <= current)
                ),
            )
            .order_by(col(ArtifactDeployment.created_at))
            .with_for_update(skip_locked=True)
        ).all()
        result: list[ArtifactDeployment] = []
        for deployment in deployments:
            release = self.session.get(ArtifactRelease, deployment.release_id)
            if release is None or release.valid_until <= current:
                deployment.status = DeploymentStatus.EXPIRED
                deployment.error = {"code": "release_expired_before_dispatch"}
            else:
                deployment.dispatch_connection_id = connection_id
                deployment.dispatch_reserved_until = current + timedelta(
                    seconds=ttl_seconds
                )
                result.append(deployment)
            self.session.add(deployment)
        self.session.commit()
        return result

    def release_reservation(
        self, deployment_id: uuid.UUID, connection_id: uuid.UUID
    ) -> None:
        deployment = self.session.exec(
            select(ArtifactDeployment)
            .where(ArtifactDeployment.id == deployment_id)
            .with_for_update()
        ).first()
        if (
            deployment
            and deployment.status == DeploymentStatus.PENDING
            and deployment.dispatch_connection_id == connection_id
        ):
            deployment.dispatch_connection_id = None
            deployment.dispatch_reserved_until = None
            self.session.add(deployment)
            self.session.commit()

    def mark_dispatched(
        self, deployment_id: uuid.UUID, connection_id: uuid.UUID
    ) -> bool:
        deployment = self.session.exec(
            select(ArtifactDeployment)
            .where(ArtifactDeployment.id == deployment_id)
            .with_for_update()
        ).first()
        if (
            deployment is None
            or deployment.status != DeploymentStatus.PENDING
            or deployment.dispatch_connection_id != connection_id
        ):
            self.session.rollback()
            return False
        deployment.status = DeploymentStatus.DISPATCHED
        deployment.dispatched_at = datetime.now(timezone.utc)
        deployment.dispatch_reserved_until = None
        self.session.add(deployment)
        self.session.commit()
        return True

    def retry(self, deployment: ArtifactDeployment) -> ArtifactDeployment:
        attempts = self.session.exec(
            select(ArtifactDeployment.attempt).where(
                ArtifactDeployment.release_id == deployment.release_id,
                ArtifactDeployment.node_id == deployment.node_id,
            )
        ).all()
        retried = ArtifactDeployment(
            namespace_id=deployment.namespace_id,
            release_id=deployment.release_id,
            node_id=deployment.node_id,
            artifact_id=deployment.artifact_id,
            logical_target=deployment.logical_target,
            previous_artifact_id=deployment.previous_artifact_id,
            attempt=max(attempts, default=0) + 1,
        )
        self.session.add(retried)
        self.session.commit()
        self.session.refresh(retried)
        return retried
