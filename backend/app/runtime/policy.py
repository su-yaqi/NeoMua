from enum import Enum

from app.models import NamespaceRole


class RuntimeAction(str, Enum):
    READ = "read"
    EXECUTE = "execute"
    MANAGE = "manage"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    REJECTED = "rejected"


class InvalidTaskTransition(ValueError):
    pass


_ACTIONS = {
    NamespaceRole.ADMIN: set(RuntimeAction),
    NamespaceRole.DEVELOPER: {RuntimeAction.READ, RuntimeAction.EXECUTE},
    NamespaceRole.USER: set(),
}

_TRANSITIONS = {
    TaskStatus.QUEUED: {TaskStatus.DISPATCHED, TaskStatus.CANCELLED, TaskStatus.REJECTED},
    TaskStatus.DISPATCHED: {TaskStatus.RUNNING, TaskStatus.REJECTED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.CANCELLING,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
    },
    TaskStatus.CANCELLING: {TaskStatus.CANCELLED, TaskStatus.FAILED},
}


def authorize_runtime_action(role: NamespaceRole, action: RuntimeAction) -> bool:
    return action in _ACTIONS[role]


def require_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in _TRANSITIONS.get(current, set()):
        raise InvalidTaskTransition(f"invalid task transition: {current} -> {target}")
