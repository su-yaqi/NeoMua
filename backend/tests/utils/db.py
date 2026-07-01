from sqlmodel import Session, delete

from app.core.config import settings
from app.models import Item, LlmProviderConfig, LlmProviderModel, User
from app.runtime.models import (
    AgentEvent,
    AgentSession,
    AgentTask,
    NodeCredential,
    NodeEnrollmentToken,
    RuntimeNode,
    RuntimeProfile,
    RuntimeSecret,
)


def cleanup_test_data(session: Session) -> None:
    session.execute(delete(AgentEvent))
    session.execute(delete(AgentTask))
    session.execute(delete(AgentSession))
    session.execute(delete(NodeCredential))
    session.execute(delete(NodeEnrollmentToken))
    session.execute(delete(RuntimeNode))
    session.execute(delete(RuntimeSecret))
    session.execute(delete(RuntimeProfile))
    session.execute(delete(LlmProviderModel))
    session.execute(delete(LlmProviderConfig))
    session.execute(delete(Item))
    session.execute(delete(User).where(User.email != settings.FIRST_SUPERUSER))
    session.commit()
