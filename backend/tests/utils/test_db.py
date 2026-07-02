from sqlmodel import Session, SQLModel, create_engine, select

from app import crud
from app.core.config import settings
from app.models import User, UserCreate
from tests.utils.db import cleanup_test_data


def test_cleanup_test_data_preserves_first_superuser() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        superuser = crud.create_user(
            session=session,
            user_create=UserCreate(
                email=settings.FIRST_SUPERUSER,
                password=settings.FIRST_SUPERUSER_PASSWORD,
                is_superuser=True,
            ),
        )
        normal_user = crud.create_user(
            session=session,
            user_create=UserCreate(
                email="temp-user@example.com", password="changethis"
            ),
        )

        cleanup_test_data(session)

        remaining_users = session.exec(select(User)).all()

        assert [user.email for user in remaining_users] == [superuser.email]
        assert remaining_users[0].id == superuser.id
        assert remaining_users[0].id != normal_user.id
