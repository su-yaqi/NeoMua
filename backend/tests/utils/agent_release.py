import uuid

from sqlmodel import Session

from app.agent_management.capability_models import AgentRelease, RuntimeAgentRelease
from app.agent_management.models import AgentDefinition


def create_active_agent_binding(
    session: Session,
    *,
    namespace_id: uuid.UUID,
    runtime_profile_id: uuid.UUID,
    provider_config_id: uuid.UUID | None,
    model_id: str,
) -> RuntimeAgentRelease:
    agent = AgentDefinition(
        namespace_id=namespace_id,
        slug=f"test-agent-{uuid.uuid4().hex[:8]}",
        name="Test Agent",
    )
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    spec = {
        "schema_version": "1.0",
        "agent_id": str(agent.id),
        "harness_type": "claude_code",
        "harness_adapter_version": "claude-code-1.0",
        "model": {
            "provider_config_id": str(provider_config_id),
            "model_id": model_id,
        },
        "system_prompt": "Test system prompt",
        "policies": {
            "permission_mode": "default",
            "timeout_seconds": 3600,
            "working_directory_strategy": "inherit",
        },
        "skills": [],
        "plugins": [],
        "tools": [],
        "mcp_servers": [],
        "version_constraints": {"cli": ">=1.0.0", "sdk": ">=0.2.0"},
        "required_capabilities": {"harness": "claude_code", "tools": []},
    }
    release = AgentRelease(
        namespace_id=namespace_id,
        agent_id=agent.id,
        version="1.0.0",
        draft_revision=1,
        resolved_spec_schema_version="1.0",
        resolved_spec=spec,
        resolved_spec_digest=digest,
        manifest={"materialization": {"resolved_spec_digest": digest}},
        dependency_lock={},
        manifest_digest=digest,
        signature="test",
        signing_public_key="test",
        idempotency_key=str(uuid.uuid4()),
    )
    binding = RuntimeAgentRelease(
        namespace_id=namespace_id,
        runtime_profile_id=runtime_profile_id,
        agent_id=agent.id,
        current_release_id=release.id,
        applied_digest=digest,
        materialization_digest=digest,
    )
    session.add(agent)
    session.flush()
    session.add(release)
    session.flush()
    session.add(binding)
    session.commit()
    session.refresh(binding)
    return binding
