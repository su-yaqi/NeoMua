from app.runtime.models import AgentEvent, RuntimeProfile


def test_platform_runtime_is_unique_per_namespace() -> None:
    indexes = {index.name: index for index in RuntimeProfile.__table__.indexes}
    index = indexes["uq_runtime_profile_namespace_platform"]
    assert index.unique
    assert index.dialect_options["postgresql"]["where"] is not None


def test_agent_event_sequence_is_unique_per_task() -> None:
    constraints = {constraint.name for constraint in AgentEvent.__table__.constraints}
    assert "uq_agent_event_task_sequence" in constraints
