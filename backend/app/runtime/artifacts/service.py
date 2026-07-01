import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

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
            .order_by(ArtifactDeployment.created_at)
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
            previous_artifact_id=deployment.previous_artifact_id,
            attempt=max(attempts, default=0) + 1,
        )
        self.session.add(retried)
        self.session.commit()
        self.session.refresh(retried)
        return retried
