from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.agent_management.models import (
    AgentDraft,
    AgentDefinition,
    HarnessProfile,
)
from app.models import RefreshSession
from app.runtime.models import (
    AgentEvent,
    AgentSession,
    AgentTask,
    ArtifactDeployment,
    ArtifactRelease,
    NodeCredential,
    NodeEnrollmentToken,
    NodeHandshakeNonce,
    RuntimeArtifact,
    RuntimeNode,
    RuntimeNodeArtifact,
    RuntimeProfile,
    RuntimeSecret,
)
from tests.utils.db import cleanup_test_data
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        SQLModel.metadata.create_all(engine)
        init_db(session)
        yield session
        cleanup_test_data(session)


@pytest.fixture(autouse=True)
def isolate_runtime_data(db: Session) -> Generator[None, None, None]:
    def clean() -> None:
        db.rollback()
        db.execute(delete(AgentDraft))
        db.execute(delete(AgentDefinition))
        db.execute(delete(HarnessProfile))
        db.execute(delete(AgentEvent))
        db.execute(delete(AgentTask))
        db.execute(delete(AgentSession))
        db.execute(delete(RefreshSession))
        db.execute(delete(ArtifactDeployment))
        db.execute(delete(RuntimeNodeArtifact))
        db.execute(delete(ArtifactRelease))
        db.execute(delete(RuntimeArtifact))
        db.execute(delete(NodeCredential))
        db.execute(delete(NodeEnrollmentToken))
        db.execute(delete(NodeHandshakeNonce))
        db.execute(delete(RuntimeNode))
        db.execute(delete(RuntimeSecret))
        db.execute(delete(RuntimeProfile))
        db.commit()
        db.expire_all()

    clean()
    yield
    clean()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
