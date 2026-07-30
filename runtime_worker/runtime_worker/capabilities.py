import hashlib
import json
import os
import re
import subprocess
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType

claude_agent_sdk: ModuleType | None
try:
    claude_agent_sdk = import_module("claude_agent_sdk")
except ModuleNotFoundError:  # client node distributions intentionally omit it
    claude_agent_sdk = None


class CapabilityDetectionError(RuntimeError):
    pass


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError as exc:
        raise CapabilityDetectionError(
            f"required package '{name}' is not installed"
        ) from exc


def _claude_sdk_executable() -> Path:
    if claude_agent_sdk is None:
        raise CapabilityDetectionError("Claude Agent SDK is not installed")
    module_file = claude_agent_sdk.__file__
    if module_file is None:
        raise CapabilityDetectionError("Claude Agent SDK package path is unavailable")
    return Path(module_file).parent / "_bundled" / "claude"


def _claude_cli_version() -> str:
    executable = _claude_sdk_executable()
    if not executable.is_file():
        raise CapabilityDetectionError("Claude Code CLI executable is not installed")
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CapabilityDetectionError("Claude Code CLI version probe failed") from exc
    match = re.match(r"^([^\s]+)", completed.stdout.strip())
    if match is None:
        raise CapabilityDetectionError("Claude Code CLI returned an invalid version")
    return match.group(1)


def _mcp_executable_inventory() -> list[str]:
    try:
        registry = json.loads(os.environ.get("NEOMUA_MCP_EXECUTABLES", "{}"))
    except json.JSONDecodeError as exc:
        raise CapabilityDetectionError(
            "NEOMUA_MCP_EXECUTABLES is not valid JSON"
        ) from exc
    if not isinstance(registry, dict):
        raise CapabilityDetectionError("NEOMUA_MCP_EXECUTABLES must be an object")
    result = []
    for key, path in registry.items():
        if (
            not isinstance(key, str)
            or not isinstance(path, str)
            or not Path(path).is_absolute()
            or not os.access(path, os.X_OK)
        ):
            raise CapabilityDetectionError(
                f"MCP executable inventory entry is unavailable: {key}"
            )
        result.append(key)
    return sorted(result)


def discover_runtime_capabilities(*, mode: str = "service") -> dict[str, object]:
    if mode not in {"service", "client", "platform"}:
        raise CapabilityDetectionError(f"unsupported Runtime management mode: {mode}")
    result: dict[str, object] = {"mcp_executables": _mcp_executable_inventory()}
    if mode in {"service", "platform"}:
        if claude_agent_sdk is None:
            raise CapabilityDetectionError(
                "Claude Agent SDK is required in service mode"
            )
        result["claude_agent_sdk"] = {
            "cli_version": _claude_cli_version(),
            "sdk_version": _package_version("claude-agent-sdk"),
            "harness_version": _package_version("neomua-runtime-worker"),
            "builtin_tools": ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
            "adapter_version": _package_version("neomua-runtime-worker"),
            "supports_tool_filters": True,
            "supports_per_tool_approval": True,
            "supports_mcp_injection": True,
            "permission_modes": ["default", "acceptEdits", "plan"],
        }
        return result
    # Client engine evidence is exclusively produced by the signed Adapter Registry.
    # This legacy heartbeat inventory must never search PATH or disclose local paths.
    return result


def node_agent_version() -> str:
    return _package_version("neomua-node-runtime")


def _executable_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_runtime_installations(*, mode: str = "service") -> list[dict[str, object]]:
    if mode != "service":
        raise CapabilityDetectionError(
            "client Runtime discovery requires the signed Adapter Registry"
        )
    capabilities = discover_runtime_capabilities(mode=mode)
    installations: list[dict[str, object]] = []
    if claude_agent_sdk is None:
        raise CapabilityDetectionError("Claude Agent SDK is required in service mode")
    claude_executable = _claude_sdk_executable()
    claude = capabilities["claude_agent_sdk"]
    assert isinstance(claude, dict)
    installations.append(
        {
            "installation_key": "service:claude-agent-sdk",
            "name": "Claude Agent SDK (NeoMua managed)",
            "engine_type": "claude_agent_sdk",
            "engine_version": claude["cli_version"],
            "adapter_version": claude["adapter_version"],
            "executable_fingerprint": _executable_fingerprint(claude_executable),
            "capabilities": {
                "sdk_version": claude["sdk_version"],
                "harness_version": claude["harness_version"],
                "tools": claude["builtin_tools"],
                "permission_modes": claude["permission_modes"],
                "supports_tool_filters": claude["supports_tool_filters"],
                "supports_per_tool_approval": claude["supports_per_tool_approval"],
                "supports_mcp_injection": claude["supports_mcp_injection"],
            },
            "discovered_models": [],
        }
    )
    return installations
