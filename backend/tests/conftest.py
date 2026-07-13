from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, delete

from app.agent_management.capability_models import (
    AgentActivation,
    AgentDeployment,
    AgentDraftMcp,
    AgentDraftPlugin,
    AgentDraftSkill,
    AgentDraftToolPolicy,
    AgentRelease,
    AgentReleaseComponent,
    CliSession,
    McpPlatformSecret,
    McpRuntimeEvent,
    McpRuntimeInstance,
    McpServer,
    McpServerRevision,
    McpTargetBinding,
    McpToolSnapshot,
    McpValidationAttempt,
    NamespaceToolPolicy,
    Plugin,
    PluginDraft,
    PluginVersion,
    RuntimeAgentRelease,
    SkillDefinition,
    SkillVersion,
    ToolApprovalRequest,
    ToolDefinition,
)
from app.agent_management.models import (
    AgentDefinition,
    AgentDraft,
    HarnessProfile,
)
from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
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
        db.execute(delete(ToolApprovalRequest))
        db.execute(delete(CliSession))
        db.execute(delete(AgentReleaseComponent))
        db.execute(delete(AgentDeployment))
        db.execute(delete(RuntimeAgentRelease))
        db.execute(delete(AgentActivation))
        db.execute(delete(AgentDraftPlugin))
        db.execute(delete(AgentDraftMcp))
        db.execute(delete(AgentDraftSkill))
        db.execute(delete(AgentDraftToolPolicy))
        db.execute(delete(McpToolSnapshot))
        db.execute(delete(McpValidationAttempt))
        db.execute(delete(McpRuntimeEvent))
        db.execute(delete(McpRuntimeInstance))
        db.execute(delete(McpPlatformSecret))
        db.execute(delete(McpTargetBinding))
        db.execute(delete(PluginVersion))
        db.execute(delete(PluginDraft))
        db.execute(delete(Plugin))
        db.execute(delete(McpServerRevision))
        db.execute(delete(McpServer))
        db.execute(delete(SkillVersion))
        db.execute(delete(SkillDefinition))
        db.execute(delete(NamespaceToolPolicy))
        db.execute(delete(ToolDefinition))
        db.execute(delete(AgentRelease))
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
