from datetime import timedelta

import pytest
from sqlmodel import Session

from app.runtime.enrollment import (
    EnrollmentTokenInvalid,
    consume_enrollment_token,
    create_enrollment_token,
)
from tests.api.routes.test_namespaces import create_namespace
from tests.utils.user import create_random_user


def test_enrollment_token_is_hashed_and_single_use(db: Session) -> None:
    namespace = create_namespace(db)
    admin = create_random_user(db)
    raw, record = create_enrollment_token(
        db, namespace.id, admin.id, ttl=timedelta(minutes=10)
    )
    assert record.token_hash != raw
    consumed = consume_enrollment_token(db, raw)
    assert consumed.id == record.id
    with pytest.raises(EnrollmentTokenInvalid):
        consume_enrollment_token(db, raw)


def test_expired_enrollment_token_is_rejected(db: Session) -> None:
    namespace = create_namespace(db)
    admin = create_random_user(db)
    raw, _ = create_enrollment_token(
        db, namespace.id, admin.id, ttl=timedelta(seconds=-1)
    )
    with pytest.raises(EnrollmentTokenInvalid):
        consume_enrollment_token(db, raw)
