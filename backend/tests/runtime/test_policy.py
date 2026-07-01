import pytest

from app.models import NamespaceRole
from app.runtime.policy import (
    InvalidTaskTransition,
    RuntimeAction,
    TaskStatus,
    authorize_runtime_action,
    require_task_transition,
)


def test_developer_can_execute_but_cannot_manage() -> None:
    assert authorize_runtime_action(NamespaceRole.DEVELOPER, RuntimeAction.EXECUTE)
    assert not authorize_runtime_action(NamespaceRole.DEVELOPER, RuntimeAction.MANAGE)


def test_user_cannot_access_runtime() -> None:
    assert not authorize_runtime_action(NamespaceRole.USER, RuntimeAction.READ)


def test_task_cannot_leave_terminal_state() -> None:
    with pytest.raises(InvalidTaskTransition):
        require_task_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
