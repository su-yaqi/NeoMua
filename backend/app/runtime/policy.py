from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    REJECTED = "rejected"


class TaskKind(str, Enum):
    ORDINARY = "ordinary"
    ADMIN = "admin"


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    DONT_ASK = "dontAsk"


class InvalidTaskTransition(ValueError):
    pass


_TRANSITIONS = {
    TaskStatus.QUEUED: {
        TaskStatus.DISPATCHED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
    },
    TaskStatus.DISPATCHED: {
        TaskStatus.RUNNING,
        TaskStatus.REJECTED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.CANCELLING,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
    },
    TaskStatus.AWAITING_APPROVAL: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLING,
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
    },
    TaskStatus.CANCELLING: {TaskStatus.CANCELLED, TaskStatus.FAILED},
}


def require_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in _TRANSITIONS.get(current, set()):
        raise InvalidTaskTransition(f"invalid task transition: {current} -> {target}")
