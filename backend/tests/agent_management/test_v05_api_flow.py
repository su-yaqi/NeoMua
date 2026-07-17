import io
import uuid
import zipfile

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.agent_management.capabilities import runtime_capability_fingerprint
from app.core.config import settings
from app.runtime.models import RuntimeProfile
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def _skill_zip(slug: str) -> bytes:
    target = io.BytesIO()
    manifest = f"""---
name: {slug}
description: Managed test skill
version: 1.0.0
platforms: [linux, darwin]
invocation_mode: discoverable
required_tools: [Read]
required_mcp_tools: []
config_schema: {{}}
content_types: [md]
source: internal
---
# Managed Skill
"""
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("SKILL.md", manifest)
    return target.getvalue()


def _configured_platform(
    client: TestClient,
    db: Session,
    headers: dict[str, str],
) -> tuple[dict, dict, dict]:
    profile = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "v05-platform"},
        headers=headers,
    )
    assert profile.status_code == 201, profile.text
    provider = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        json={
            "config_name": "v05-provider",
            "provider_slug": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "enabled": True,
            "secret_inputs": {"api_token": "sk-v05-test-secret"},
            "extra_config": {},
            "manual_models": [{"model_id": "deepseek-chat"}],
            "enabled_model_ids": ["deepseek-chat"],
        },
        headers=headers,
    )
    assert provider.status_code == 200, provider.text
    runtime = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        json={
            "route_mode": "platform_gateway",
            "provider_config_id": provider.json()["id"],
            "model_id": "deepseek-chat",
        },
        headers=headers,
    )
    assert runtime.status_code == 200, runtime.text
    report = client.post(
        f"{settings.API_V1_STR}/internal/runtime/capabilities",
        json={
            "worker_id": "v05-api-test",
            "harness_capabilities": {
                "claude_code": {
                    "cli_version": "2.1.191",
                    "sdk_version": "0.2.110",
                    "harness_version": "0.1.0",
                    "builtin_tools": [
                        "Bash",
                        "Edit",
                        "Glob",
                        "Grep",
                        "Read",
                        "Write",
                    ],
                },
                "mcp_executables": [],
            },
        },
        headers={"X-Runtime-Token": settings.INTERNAL_RUNTIME_TOKEN},
    )
    assert report.status_code == 200, report.text
    db.expire_all()
    return profile.json(), provider.json(), runtime.json()


def test_v05_managed_capability_release_and_precheck_flow(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "ARTIFACT_LOCAL_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "ARTIFACT_TEMP_DIR", str(tmp_path / "temporary"))
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    profile, provider, runtime = _configured_platform(client, db, headers)

    suffix = uuid.uuid4().hex[:8]
    skill_slug = f"managed-skill-{suffix}"
    skill_result = client.post(
        f"{settings.API_V1_STR}/skills/complete",
        data={
            "slug": skill_slug,
            "name": "Managed Skill",
            "version": "1.0.0",
        },
        files={"file": ("skill.zip", _skill_zip(skill_slug), "application/zip")},
        headers=headers,
    )
    assert skill_result.status_code == 201, skill_result.text
    skill = skill_result.json()["skill"]
    skill_version = skill_result.json()["version"]
    assert skill_version["validation_result"]["status"] == "validated"

    mcp = client.post(
        f"{settings.API_V1_STR}/mcp-servers",
        json={"slug": f"managed-mcp-{suffix}", "name": "Managed MCP"},
        headers=headers,
    )
    assert mcp.status_code == 201, mcp.text
    revision = client.post(
        f"{settings.API_V1_STR}/mcp-servers/{mcp.json()['id']}/revisions",
        json={
            "transport": "streamable_http",
            "config": {"endpoint": "https://mcp.example/tools"},
        },
        headers=headers,
    )
    assert revision.status_code == 201, revision.text
    target = client.post(
        f"{settings.API_V1_STR}/mcp-revisions/{revision.json()['id']}/targets",
        json={"runtime_profile_id": runtime["id"]},
        headers=headers,
    )
    assert target.status_code == 201, target.text
    secret = client.put(
        f"{settings.API_V1_STR}/mcp-targets/{target.json()['id']}/secret",
        json={"secret_inputs": {"Authorization": "Bearer test-only"}},
        headers=headers,
    )
    assert secret.status_code == 200, secret.text
    validation = client.post(
        f"{settings.API_V1_STR}/mcp-targets/{target.json()['id']}/validate",
        headers=headers,
    )
    assert validation.status_code == 202, validation.text
    claim = client.post(
        f"{settings.API_V1_STR}/internal/runtime/mcp-validations/claim",
        headers={"X-Runtime-Token": settings.INTERNAL_RUNTIME_TOKEN},
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["secret_inputs"] == {"Authorization": "Bearer test-only"}
    runtime_row = db.get(RuntimeProfile, uuid.UUID(runtime["id"]))
    assert runtime_row is not None
    result = client.post(
        f"{settings.API_V1_STR}/internal/runtime/mcp-validations/{validation.json()['id']}/result",
        json={
            "status": "verified",
            "capability_fingerprint": runtime_capability_fingerprint(runtime_row),
            "tools": [
                {
                    "name": "search",
                    "description": "Search managed data",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
        },
        headers={"X-Runtime-Token": settings.INTERNAL_RUNTIME_TOKEN},
    )
    assert result.status_code == 200, result.text
    assert result.json()["target_status"] == "verified"
    validations = client.get(
        f"{settings.API_V1_STR}/mcp-targets/{target.json()['id']}/validations",
        headers=headers,
    )
    assert validations.status_code == 200, validations.text
    qualified_tool = validations.json()["data"][0]["tools"][0]["qualified_name"]

    plugin = client.post(
        f"{settings.API_V1_STR}/plugins",
        json={"slug": f"managed-plugin-{suffix}", "name": "Managed Plugin"},
        headers=headers,
    )
    assert plugin.status_code == 201, plugin.text
    plugin_draft = client.put(
        f"{settings.API_V1_STR}/plugins/{plugin.json()['id']}/draft",
        json={
            "expected_revision": 1,
            "harness_type": "claude_code",
            "adapter_schema_version": "1.0",
            "adapter_config": {},
            "components": [
                {
                    "type": "tool_policy",
                    "tool_key": "Bash",
                    "policy": "require_approval",
                }
            ],
        },
        headers=headers,
    )
    assert plugin_draft.status_code == 200, plugin_draft.text
    plugin_validation = client.post(
        f"{settings.API_V1_STR}/plugins/{plugin.json()['id']}/draft/validate",
        headers=headers,
    )
    assert plugin_validation.json()["status"] == "validated"
    plugin_version = client.post(
        f"{settings.API_V1_STR}/plugins/{plugin.json()['id']}/versions",
        json={"draft_revision": 2, "version": "1.0.0"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert plugin_version.status_code == 201, plugin_version.text
    assert plugin_version.json()["signature"]

    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": f"managed-agent-{suffix}", "name": "Managed Agent"},
        headers=headers,
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]
    draft = client.put(
        f"{settings.API_V1_STR}/agents/{agent_id}/draft",
        json={
            "expected_revision": 1,
            "harness_profile_id": profile["id"],
            "provider_config_id": provider["id"],
            "model_id": "deepseek-chat",
            "system_prompt": "Use only the frozen managed capabilities.",
        },
        headers=headers,
    )
    assert draft.status_code == 200, draft.text
    bound_skills = client.put(
        f"{settings.API_V1_STR}/agents/{agent_id}/draft/skills",
        json={
            "expected_revision": 2,
            "skills": [{"skill_id": skill["id"], "enabled": True}],
        },
        headers=headers,
    )
    assert bound_skills.status_code == 200, bound_skills.text
    bound_plugins = client.put(
        f"{settings.API_V1_STR}/agents/{agent_id}/draft/plugins",
        json={
            "expected_revision": 3,
            "plugins": [{"plugin_version_id": plugin_version.json()["id"]}],
        },
        headers=headers,
    )
    assert bound_plugins.status_code == 200, bound_plugins.text
    bound_mcp = client.put(
        f"{settings.API_V1_STR}/agents/{agent_id}/draft/mcp",
        json={
            "expected_revision": 4,
            "mcp": [
                {
                    "revision_id": revision.json()["id"],
                    "allowed_tools": [qualified_tool],
                }
            ],
        },
        headers=headers,
    )
    assert bound_mcp.status_code == 200, bound_mcp.text
    validation = client.post(
        f"{settings.API_V1_STR}/agents/{agent_id}/draft/validate",
        headers=headers,
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["status"] == "validated"

    release = client.post(
        f"{settings.API_V1_STR}/agents/{agent_id}/releases",
        json={"draft_revision": 5, "version": "1.0.0"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert release.status_code == 201, release.text
    detail = client.get(
        f"{settings.API_V1_STR}/agent-releases/{release.json()['id']}",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["signature_valid"] is True
    assert {item["type"] for item in detail.json()["components"]} >= {
        "skill",
        "plugin",
        "mcp",
    }

    precheck = client.post(
        f"{settings.API_V1_STR}/agent-releases/{release.json()['id']}/activations/precheck",
        json={"runtime_profile_ids": [runtime["id"]]},
        headers=headers,
    )
    assert precheck.status_code == 200, precheck.text
    assert precheck.json()["compatible"] is True, precheck.text
    assert precheck.json()["targets"][0]["status"] == "pending"

    activation = client.post(
        f"{settings.API_V1_STR}/agent-releases/{release.json()['id']}/activations",
        json={"runtime_profile_ids": [runtime["id"]]},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert activation.status_code == 202, activation.text
    assert activation.json()["status"] == "deploying"
