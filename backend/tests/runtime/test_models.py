from app.runtime.models import AgentEvent, RuntimeProfile


def test_platform_runtime_is_unique_per_namespace() -> None:
    constraints = {constraint.name for constraint in RuntimeProfile.__table__.constraints}
    assert "uq_runtime_profile_namespace_platform" in constraints


def test_agent_event_sequence_is_unique_per_task() -> None:
    constraints = {constraint.name for constraint in AgentEvent.__table__.constraints}
    assert "uq_agent_event_task_sequence" in constraints
