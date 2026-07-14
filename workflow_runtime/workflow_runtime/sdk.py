from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowRunContext:
    workflow_instance_id: str
    node_key: str
    runtime_id: str
    project_id: str | None
    input: dict[str, Any]
    previous_output: dict[str, Any] | None
    change_summary: dict[str, Any]
    idempotency_key: str


NodeHandler = Callable[[WorkflowRunContext], dict[str, Any]]
Validator = Callable[[WorkflowRunContext], tuple[bool, dict[str, Any]]]
EdgeCondition = Callable[[dict[str, Any], dict[str, Any]], bool]

NODE_HANDLERS: dict[str, NodeHandler] = {}
VALIDATORS: dict[str, Validator] = {}


def _always(_output: dict[str, Any], _config: dict[str, Any]) -> bool:
    return True


def _equals(output: dict[str, Any], config: dict[str, Any]) -> bool:
    field = config.get("field")
    return isinstance(field, str) and output.get(field) == config.get("value")


EDGE_CONDITIONS: dict[str, EdgeCondition] = {
    "always": _always,
    "equals": _equals,
}
FRONTEND_COMPONENTS: set[str] = {
    "workflow.project_delivery",
    "workflow.project_delivery.v1_0_3",
    "workflow.project_delivery.v1_0_4",
    "workflow.project_delivery.v1_0_5",
    "workflow.project_delivery.v1_0_6",
    "workflow.web_platform_development.v1_0_0",
    "workflow.web_platform_development.v1_0_1",
}


def register_node_handler(key: str) -> Callable[[NodeHandler], NodeHandler]:
    def decorator(handler: NodeHandler) -> NodeHandler:
        if key in NODE_HANDLERS:
            raise ValueError(f"Duplicate Workflow node handler: {key}")
        NODE_HANDLERS[key] = handler
        return handler

    return decorator


def register_validator(key: str) -> Callable[[Validator], Validator]:
    def decorator(validator: Validator) -> Validator:
        if key in VALIDATORS:
            raise ValueError(f"Duplicate Workflow validator: {key}")
        VALIDATORS[key] = validator
        return validator

    return decorator


def register_edge_condition(key: str) -> Callable[[EdgeCondition], EdgeCondition]:
    def decorator(condition: EdgeCondition) -> EdgeCondition:
        if key in EDGE_CONDITIONS:
            raise ValueError(f"Duplicate Workflow edge condition: {key}")
        EDGE_CONDITIONS[key] = condition
        return condition

    return decorator


def register_frontend_component(key: str) -> None:
    FRONTEND_COMPONENTS.add(key)
