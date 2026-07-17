import hashlib
import json
import os
import re
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import claude_agent_sdk


class CapabilityDetectionError(RuntimeError):
    pass


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError as exc:
        raise CapabilityDetectionError(
            f"required package '{name}' is not installed"
        ) from exc


def _claude_cli_version() -> str:
    executable = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
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


def _codex_cli_capability() -> dict[str, object] | None:
    executable = shutil.which("codex")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CapabilityDetectionError("Codex CLI version probe failed") from exc
    match = re.search(r"([0-9]+(?:\.[0-9A-Za-z-]+)+)", completed.stdout)
    if match is None:
        raise CapabilityDetectionError("Codex CLI returned an invalid version")
    return {
        "cli_version": match.group(1),
        "adapter_version": _package_version("neomua-runtime-worker"),
        "builtin_tools": [],
        "supports_tool_filters": False,
        "supports_per_tool_approval": False,
        "supports_mcp_injection": False,
        "permission_modes": ["default", "acceptEdits", "plan"],
    }


def discover_runtime_capabilities() -> dict[str, object]:
    result: dict[str, object] = {
        "claude_code": {
            "cli_version": _claude_cli_version(),
            "sdk_version": _package_version("claude-agent-sdk"),
            "harness_version": _package_version("neomua-runtime-worker"),
            "builtin_tools": ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
            "adapter_version": _package_version("neomua-runtime-worker"),
            "supports_tool_filters": True,
            "supports_per_tool_approval": True,
            "supports_mcp_injection": True,
            "permission_modes": ["default", "acceptEdits", "plan"],
        },
        "mcp_executables": _mcp_executable_inventory(),
    }
    codex = _codex_cli_capability()
    if codex is not None:
        result["codex"] = codex
    return result


def node_agent_version() -> str:
    return _package_version("neomua-node-runtime")


def _executable_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_runtime_installations() -> list[dict[str, object]]:
    capabilities = discover_runtime_capabilities()
    installations: list[dict[str, object]] = []
    claude_executable = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    claude = capabilities["claude_code"]
    assert isinstance(claude, dict)
    installations.append(
        {
            "installation_key": "claude_code:bundled",
            "name": "Claude Code (bundled)",
            "engine_type": "claude_code",
            "engine_version": claude["cli_version"],
            "adapter_version": claude["adapter_version"],
            "executable_fingerprint": _executable_fingerprint(claude_executable),
            "capabilities": {
                "tools": claude["builtin_tools"],
                "permission_modes": claude["permission_modes"],
                "supports_tool_filters": claude["supports_tool_filters"],
                "supports_per_tool_approval": claude["supports_per_tool_approval"],
                "supports_mcp_injection": claude["supports_mcp_injection"],
            },
            "discovered_models": [],
        }
    )
    codex = capabilities.get("codex")
    codex_executable = shutil.which("codex")
    if isinstance(codex, dict) and codex_executable:
        installations.append(
            {
                "installation_key": "codex:default",
                "name": "Codex",
                "engine_type": "codex",
                "engine_version": codex["cli_version"],
                "adapter_version": codex["adapter_version"],
                "executable_fingerprint": _executable_fingerprint(Path(codex_executable)),
                "capabilities": {
                    "tools": codex["builtin_tools"],
                    "permission_modes": codex["permission_modes"],
                    "supports_tool_filters": codex["supports_tool_filters"],
                    "supports_per_tool_approval": codex["supports_per_tool_approval"],
                    "supports_mcp_injection": codex["supports_mcp_injection"],
                },
                "discovered_models": [],
            }
        )
    return installations
