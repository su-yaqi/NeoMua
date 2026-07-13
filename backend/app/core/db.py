from sqlmodel import Session, create_engine, select

from app import crud
from app.core.config import settings
from app.models import User, UserCreate

engine = create_engine(str(settings.SQLALCHEMY_DATABASE_URI))


# make sure all SQLModel models are imported (app.models) before initializing DB
# otherwise, SQLModel might fail to initialize relationships properly
# for more details: https://github.com/fastapi/full-stack-fastapi-template/issues/28


def init_db(session: Session) -> None:
    # Tables should be created with Alembic migrations
    # But if you don't want to use migrations, create
    # the tables un-commenting the next lines
    # from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    # SQLModel.metadata.create_all(engine)

    user = session.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            is_superuser=True,
        )
        user = crud.create_user(session=session, user_create=user_in)

    # Bundled Workflow code is part of the deployed build. Register only its
    # canonical manifests; executable source is never accepted from the API.
    from app.workflow_management.bundled import bundled_manifests
    from app.workflow_management.package import package_digest, validate_package
    from app.workflow_management.routes import sync_registry
    from app.workflow_management.schemas import RegistrySync

    for raw_manifest in bundled_manifests():
        manifest = validate_package(raw_manifest)
        sync_registry(
            RegistrySync(
                manifest=raw_manifest,
                package_digest=package_digest(manifest),
                build_metadata={"source": "bundled", "validated": True},
            ),
            session,
        )
