from workflow_runtime.sdk import WorkflowRunContext, register_validator


@register_validator("project_delivery.has_clarification")
def validate_in(context: WorkflowRunContext) -> tuple[bool, dict[str, object]]:
    clarification = context.input.get("upstream", {}).get("clarify", {}).get("output")
    passed = bool(
        isinstance(clarification, dict)
        and clarification.get("goals")
        and clarification.get("acceptance_criteria")
    )
    return passed, {
        "code": "ok" if passed else "clarification_incomplete",
        "message": "需求澄清必须包含目标和验收标准。",
    }
