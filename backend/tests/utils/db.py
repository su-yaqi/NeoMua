from sqlmodel import Session, delete

from app.core.config import settings
from app.models import Item, LlmProviderConfig, LlmProviderModel, User


def cleanup_test_data(session: Session) -> None:
    session.execute(delete(LlmProviderModel))
    session.execute(delete(LlmProviderConfig))
    session.execute(delete(Item))
    session.execute(delete(User).where(User.email != settings.FIRST_SUPERUSER))
    session.commit()
