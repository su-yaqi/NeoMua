import hashlib
import json
import os
import re
import secrets
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime_worker.permissions import validate_permission_mode

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_PROCESS_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
}
_RESERVED_MODEL_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
}
_SECURITY_KEYS = {
    "allowed_working_roots",
    "network_policy",
    "permission_mode",
    "permission_modes",
}
_RESOURCE_KEYS = {"max_timeout_seconds"}


def _normalized_absolute_path(value: str):
    candidates = (PurePosixPath(value), PureWindowsPath(value))
    for candidate in candidates:
        if candidate.is_absolute() and ".." not in candidate.parts:
            return candidate
    return None


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def sanitize_process_environment() -> dict[str, str]:
    """Remove application credentials before an Agent SDK can inherit them."""
    source = dict(os.environ)
    safe = {key: value for key, value in source.items() if key in _SAFE_PROCESS_ENV}
    os.environ.clear()
    os.environ.update(safe)
    return source


class AppliedRuntimeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_instance_id: str
    engine_type: str
    engine_version: str | None = None
    adapter_version: str
    configuration_revision_id: str
    configuration_digest: str
    executable: str
    arguments: list[str] = Field(default_factory=list)
    working_directory_policy: str
    environment_allowlist: list[str] = Field(default_factory=list)
    security_policy: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    discovered_models: list[dict[str, Any]] = Field(default_factory=list)
    locally_validated_models: list[str] = Field(default_factory=list)
    locally_validated_routes: dict[str, str] = Field(default_factory=dict)
    capability_fingerprint: str

    @model_validator(mode="after")
    def validate_policy(self) -> "AppliedRuntimeConfiguration":
        if self.arguments:
            raise ValueError("adapter_contract_unsupported: configured base arguments")
        executable_name = Path(self.executable).name
        allowed_names = {
            "claude_code": {"claude", "claude-code"},
            "codex": {"codex"},
        }.get(self.engine_type, set())
        if executable_name not in allowed_names:
            raise ValueError("configured executable does not match Runtime engine")
        if self.executable not in {"claude", "claude-code", "codex"}:
            executable_path = Path(self.executable)
            if not executable_path.is_absolute() or not os.access(
                executable_path, os.X_OK
            ):
                raise ValueError(
                    "configured executable path must be absolute and executable"
                )
        if self.working_directory_policy not in {"workspace", "project"}:
            raise ValueError("unsupported working directory policy")
        if len(set(self.environment_allowlist)) != len(self.environment_allowlist):
            raise ValueError("environment allowlist contains duplicates")
        if any(not _ENV_NAME.fullmatch(name) for name in self.environment_allowlist):
            raise ValueError("environment allowlist contains an invalid variable name")
        unknown_security = set(self.security_policy) - _SECURITY_KEYS
        if unknown_security:
            raise ValueError(
                f"unsupported Runtime security policy: {sorted(unknown_security)}"
            )
        network_policy = self.security_policy.get("network_policy", "unrestricted")
        if network_policy != "unrestricted":
            raise ValueError(
                "adapter_contract_unsupported: restricted network policy"
            )
        modes = self.security_policy.get("permission_modes")
        if modes is None and self.security_policy.get("permission_mode") is not None:
            modes = [self.security_policy["permission_mode"]]
        if modes is not None:
            if not isinstance(modes, list) or not modes:
                raise ValueError("Runtime permission_modes must be a non-empty list")
            for mode in modes:
                validate_permission_mode(str(mode))
        roots = self.security_policy.get("allowed_working_roots", [])
        if not isinstance(roots, list) or any(
            not isinstance(root, str)
            or _normalized_absolute_path(root) is None
            for root in roots
        ):
            raise ValueError("allowed_working_roots must contain normalized absolute paths")
        unknown_limits = set(self.resource_limits) - _RESOURCE_KEYS
        if unknown_limits:
            raise ValueError(
                f"adapter_contract_unsupported: resource limits {sorted(unknown_limits)}"
            )
        for key in _RESOURCE_KEYS:
            value = self.resource_limits.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{key} must be a positive integer")
        return self

    def allowed_permission_modes(self) -> set[str]:
        values = self.security_policy.get("permission_modes")
        if values is None and self.security_policy.get("permission_mode") is not None:
            values = [self.security_policy["permission_mode"]]
        return {str(value) for value in (values or [])}

    def execution_path(self) -> str | None:
        """Use adapter-owned discovery for canonical names, exact paths otherwise."""
        if self.executable in {"claude", "claude-code", "codex"}:
            return None
        return self.executable

    def task_environment(self, source: dict[str, str]) -> dict[str, str]:
        return {
            name: source[name]
            for name in self.environment_allowlist
            if name in source and name not in _RESERVED_MODEL_ENV
        }

    def validate_task_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if str(snapshot.get("runtime_instance_id")) != self.runtime_instance_id:
            raise ValueError("runtime_instance_mismatch")
        if snapshot.get("engine_type") != self.engine_type:
            raise ValueError("runtime_engine_mismatch")
        if snapshot.get("engine_version") != self.engine_version:
            raise ValueError("runtime_engine_version_mismatch")
        if snapshot.get("adapter_version") != self.adapter_version:
            raise ValueError("runtime_adapter_version_mismatch")
        if snapshot.get("runtime_configuration_digest") != self.configuration_digest:
            raise ValueError("runtime_configuration_digest_mismatch")
        if snapshot.get("capability_fingerprint") != self.capability_fingerprint:
            raise ValueError("runtime_capability_fingerprint_mismatch")
        mode = str(snapshot.get("permission_mode", "default"))
        allowed_modes = self.allowed_permission_modes()
        if allowed_modes and mode not in allowed_modes:
            raise ValueError("agent_permission_exceeds_runtime_policy")
        timeout = int(snapshot.get("timeout_seconds", 3600))
        max_timeout = self.resource_limits.get("max_timeout_seconds")
        if max_timeout is not None and timeout > int(max_timeout):
            raise ValueError("agent_timeout_exceeds_runtime_limit")
        cwd = snapshot.get("working_directory")
        roots = [str(root) for root in self.security_policy.get("allowed_working_roots", [])]
        if self.working_directory_policy == "project" and not cwd:
            raise ValueError("Runtime requires an explicit working directory")
        if cwd:
            candidate = _normalized_absolute_path(str(cwd))
            allowed = [_normalized_absolute_path(root) for root in roots]
            if candidate is None or not any(
                root is not None
                and type(candidate) is type(root)
                and (candidate == root or root in candidate.parents)
                for root in allowed
            ):
                raise ValueError("working directory is outside Runtime allowlist")
        available_tools = set(self.capabilities.get("tools", []))
        required_tools = set(snapshot.get("allowed_tools", []))
        if not required_tools.issubset(available_tools):
            raise ValueError("runtime_tool_capability_changed")
        if snapshot.get("require_approval_tools") and not self.capabilities.get(
            "supports_per_tool_approval", False
        ):
            raise ValueError("runtime_tool_approval_unsupported")
        if snapshot.get("mcp_servers") and not self.capabilities.get(
            "supports_mcp_injection", False
        ):
            raise ValueError("runtime_mcp_injection_unsupported")
        required_capabilities = snapshot.get("required_capabilities", {})
        if not isinstance(required_capabilities, dict):
            raise ValueError("required_capabilities_invalid")
        for key, required in required_capabilities.items():
            if required is True and self.capabilities.get(key) is not True:
                raise ValueError(f"runtime_capability_required:{key}")
        engine_model_id = str(snapshot.get("engine_model_id", ""))
        if not engine_model_id:
            raise ValueError("runtime_model_id_missing")
        route_type = str(snapshot["route_type"])
        route_reference = str(snapshot["route_key"])
        if route_type == "runtime_native":
            local_route = self.locally_validated_routes.get(engine_model_id)
            if local_route is None or local_route != route_reference:
                raise ValueError("runtime_native_model_route_not_validated")
            route_reference = local_route
        return {
            "runtime_instance_id": self.runtime_instance_id,
            "runtime_model_binding_id": str(snapshot["runtime_model_binding_id"]),
            "engine_type": self.engine_type,
            "engine_version": self.engine_version,
            "adapter_version": self.adapter_version,
            "engine_model_id": engine_model_id,
            "route_type": route_type,
            "route_reference": route_reference,
            "runtime_configuration_digest": self.configuration_digest,
            "capability_fingerprint": self.capability_fingerprint,
            "effective_spec_digest": str(snapshot["effective_spec_digest"]),
        }


class RuntimeConfigurationStore:
    def __init__(
        self,
        path: Path,
        *,
        source_environment: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.source_environment = source_environment or dict(os.environ)
        self.approved_environment = _SAFE_PROCESS_ENV | {
            name.strip()
            for name in self.source_environment.get(
                "NEOMUA_RUNTIME_ENV_ALLOWLIST", ""
            ).split(",")
            if name.strip()
        }
        if any(
            not _ENV_NAME.fullmatch(name) or name in _RESERVED_MODEL_ENV
            for name in self.approved_environment
        ):
            raise ValueError("NEOMUA_RUNTIME_ENV_ALLOWLIST is invalid")
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Runtime configuration store is invalid")
        return value

    def save(self, configuration: AppliedRuntimeConfiguration) -> None:
        with self._lock:
            values = self._read()
            values[configuration.runtime_instance_id] = configuration.model_dump()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_name(
                f".{self.path.name}.{secrets.token_hex(8)}.tmp"
            )
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(values, handle, sort_keys=True, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def get(self, runtime_instance_id: str) -> AppliedRuntimeConfiguration | None:
        with self._lock:
            value = self._read().get(runtime_instance_id)
        return AppliedRuntimeConfiguration.model_validate(value) if value else None

    def capability_evidence(self) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._read().values())
        configurations = [
            AppliedRuntimeConfiguration.model_validate(value) for value in values
        ]
        return [
            {
                "runtime_instance_id": item.runtime_instance_id,
                "engine_type": item.engine_type,
                "engine_version": item.engine_version,
                "adapter_version": item.adapter_version,
                "configuration_digest": item.configuration_digest,
                "capability_fingerprint": item.capability_fingerprint,
            }
            for item in configurations
        ]

    def apply(
        self,
        payload: dict[str, Any],
        *,
        engine_version: str | None,
        adapter_version: str,
        capabilities: dict[str, Any],
        discovered_models: list[dict[str, Any]],
    ) -> AppliedRuntimeConfiguration:
        configuration_payload = {
            "executable": payload["executable"],
            "arguments": payload.get("arguments", []),
            "working_directory_policy": payload["working_directory_policy"],
            "environment_allowlist": payload.get("environment_allowlist", []),
            "security_policy": payload.get("security_policy", {}),
            "resource_limits": payload.get("resource_limits", {}),
        }
        actual_digest = canonical_digest(configuration_payload)
        if actual_digest != payload.get("configuration_digest"):
            raise ValueError("Runtime configuration digest mismatch")
        requested_environment = set(payload.get("environment_allowlist", []))
        unavailable_environment = requested_environment - self.approved_environment
        if unavailable_environment:
            raise ValueError(
                "Runtime environment variables are not operator-approved: "
                f"{sorted(unavailable_environment)}"
            )
        capability_payload = {
            "engine_type": str(payload["engine_type"]),
            "engine_version": engine_version,
            "adapter_version": adapter_version,
            "configuration_digest": actual_digest,
            "capabilities": capabilities,
            "discovered_models": discovered_models,
        }
        configuration = AppliedRuntimeConfiguration(
            runtime_instance_id=str(payload["runtime_instance_id"]),
            engine_type=str(payload["engine_type"]),
            engine_version=engine_version,
            adapter_version=adapter_version,
            configuration_revision_id=str(payload["configuration_revision_id"]),
            configuration_digest=actual_digest,
            executable=str(payload["executable"]),
            arguments=list(payload.get("arguments", [])),
            working_directory_policy=str(payload["working_directory_policy"]),
            environment_allowlist=list(payload.get("environment_allowlist", [])),
            security_policy=dict(payload.get("security_policy", {})),
            resource_limits=dict(payload.get("resource_limits", {})),
            capabilities=capabilities,
            discovered_models=discovered_models,
            capability_fingerprint=canonical_digest(capability_payload),
        )
        self.save(configuration)
        return configuration

    def record_validated_model(
        self, runtime_instance_id: str, engine_model_id: str, route_key: str
    ) -> None:
        with self._lock:
            configuration = self.get(runtime_instance_id)
            if configuration is None:
                raise ValueError("Runtime configuration is not applied locally")
            if engine_model_id not in configuration.locally_validated_models:
                configuration.locally_validated_models.append(engine_model_id)
            configuration.locally_validated_routes[engine_model_id] = route_key
            self.save(configuration)
