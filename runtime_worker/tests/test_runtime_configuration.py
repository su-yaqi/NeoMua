from pathlib import Path

import pytest

from runtime_worker.runtime_configuration import (
    RuntimeConfigurationStore,
    canonical_digest,
)


def _applied_store(tmp_path: Path) -> RuntimeConfigurationStore:
    store = RuntimeConfigurationStore(
        tmp_path / "runtime-configurations.json",
        source_environment={
            "SAFE_VALUE": "allowed",
            "APP_SECRET": "blocked",
            "NEOMUA_RUNTIME_ENV_ALLOWLIST": "SAFE_VALUE",
        },
    )
    configuration = {
        "runtime_instance_id": "runtime-1",
        "configuration_revision_id": "configuration-1",
        "engine_type": "claude_code",
        "executable": "claude",
        "arguments": [],
        "working_directory_policy": "project",
        "environment_allowlist": ["SAFE_VALUE"],
        "security_policy": {
            "permission_modes": ["default"],
            "allowed_working_roots": ["/workspace"],
        },
        "resource_limits": {"max_timeout_seconds": 60},
    }
    configuration["configuration_digest"] = canonical_digest(
        {
            key: configuration[key]
            for key in (
                "executable",
                "arguments",
                "working_directory_policy",
                "environment_allowlist",
                "security_policy",
                "resource_limits",
            )
        }
    )
    store.apply(
        configuration,
        engine_version="2.1.191",
        adapter_version="0.1.0",
        capabilities={
            "tools": ["Read"],
            "permission_modes": ["default"],
            "supports_per_tool_approval": False,
            "supports_mcp_injection": False,
        },
        discovered_models=[{"id": "claude-test"}],
    )
    return store


def test_local_configuration_builds_evidence_and_filters_environment(
    tmp_path: Path,
) -> None:
    store = _applied_store(tmp_path)
    configuration = store.get("runtime-1")
    assert configuration is not None
    store.record_validated_model("runtime-1", "claude-test", "native")
    configuration = store.get("runtime-1")
    assert configuration is not None
    assert configuration.task_environment(store.source_environment) == {
        "SAFE_VALUE": "allowed"
    }
    evidence = configuration.validate_task_snapshot(
        {
            "runtime_instance_id": "runtime-1",
            "runtime_model_binding_id": "binding-1",
            "engine_type": "claude_code",
            "engine_version": "2.1.191",
            "adapter_version": "0.1.0",
            "runtime_configuration_digest": configuration.configuration_digest,
            "capability_fingerprint": configuration.capability_fingerprint,
            "permission_mode": "default",
            "timeout_seconds": 60,
            "working_directory": "/workspace/project",
            "allowed_tools": ["Read"],
            "route_type": "runtime_native",
            "route_key": "native",
            "engine_model_id": "claude-test",
            "effective_spec_digest": "effective-1",
        }
    )
    assert evidence["engine_version"] == "2.1.191"
    assert evidence["engine_model_id"] == "claude-test"


def test_local_configuration_rejects_server_snapshot_spoof(tmp_path: Path) -> None:
    store = _applied_store(tmp_path)
    configuration = store.get("runtime-1")
    assert configuration is not None
    with pytest.raises(ValueError, match="runtime_capability_fingerprint_mismatch"):
        configuration.validate_task_snapshot(
            {
                "runtime_instance_id": "runtime-1",
                "engine_type": "claude_code",
                "engine_version": "2.1.191",
                "adapter_version": "0.1.0",
                "runtime_configuration_digest": configuration.configuration_digest,
                "capability_fingerprint": "server-controlled-value",
            }
        )


def test_configuration_cannot_allowlist_application_secret(tmp_path: Path) -> None:
    store = RuntimeConfigurationStore(
        tmp_path / "runtime-configurations.json",
        source_environment={"APP_SECRET": "must-not-leak"},
    )
    material = {
        "executable": "claude",
        "arguments": [],
        "working_directory_policy": "workspace",
        "environment_allowlist": ["APP_SECRET"],
        "security_policy": {},
        "resource_limits": {},
    }
    with pytest.raises(ValueError, match="not operator-approved"):
        store.apply(
            {
                "runtime_instance_id": "runtime-1",
                "configuration_revision_id": "configuration-1",
                "configuration_digest": canonical_digest(material),
                "engine_type": "claude_code",
                **material,
            },
            engine_version="2.1.191",
            adapter_version="0.1.0",
            capabilities={},
            discovered_models=[],
        )
