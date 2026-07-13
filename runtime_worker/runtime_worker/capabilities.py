import json
import os
import re
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


def discover_harness_capabilities() -> dict[str, object]:
    return {
        "claude_code": {
            "cli_version": _claude_cli_version(),
            "sdk_version": _package_version("claude-agent-sdk"),
            "harness_version": _package_version("neomua-runtime-worker"),
            "builtin_tools": ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
        },
        "mcp_executables": _mcp_executable_inventory(),
    }


def node_agent_version() -> str:
    return _package_version("neomua-node-runtime")
