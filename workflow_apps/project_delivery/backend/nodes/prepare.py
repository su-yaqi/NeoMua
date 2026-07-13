from app.workflow_management.sdk import WorkflowRunContext, register_node_handler


@register_node_handler("project_delivery.prepare")
def run(context: WorkflowRunContext) -> dict[str, object]:
    clarification = context.input["upstream"]["clarify"]["output"]
    goals = clarification.get("goals", [])
    criteria = clarification.get("acceptance_criteria", [])
    return {
        "summary": f"目标 {len(goals)} 项，验收标准 {len(criteria)} 项。",
        "source_revision": int(context.input["upstream_revisions"]["clarify"]),
    }
