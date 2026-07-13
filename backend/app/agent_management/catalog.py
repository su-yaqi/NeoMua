"""Harness type catalog, environment variable policy, and config schema validation.

All security invariants here are enforced at the schema layer (not just the
frontend): bypassPermissions, shell metacharacters, secret-named keys, and
denylisted environment variables are rejected for both HarnessProfile and
AgentDraft configs. The denylist always takes precedence over the allowlist.
"""

import re
from enum import Enum
from typing import Any

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

# v0.5: only claude_code is an executable harness type. Unknown types may be
# stored as data for future migration but never pass validation as executable.
ALLOWED_HARNESS_TYPES: set[str] = {"claude_code"}

MAX_TIMEOUT_SECONDS = 3600

# Environment variables that users MAY configure (v0.5 initial set).
ENV_ALLOWLIST: set[str] = {
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "MAX_THINKING_TOKENS",
    "BASH_DEFAULT_TIMEOUT_MS",
    "BASH_MAX_TIMEOUT_MS",
    "MAX_MCP_OUTPUT_TOKENS",
    "DISABLE_TELEMETRY",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
}
# Allow CLAUDE_CODE_* prefix env names.
ENV_ALLOWLIST_PREFIXES: tuple[str, ...] = ("CLAUDE_CODE_",)

# Reserved names (not user-configurable but shown in catalog for clarity).
ENV_RESERVED: set[str] = set()

# Absolute denylist. Takes precedence over allowlist in all cases.
ENV_DENYLIST: set[str] = {
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PATH",
    "SHELL",
    "BROWSER",
    "GIT_SSH_COMMAND",
}
ENV_DENYLIST_PREFIXES: tuple[str, ...] = ("NEOMUA_", "DYLD_")

# Keys that indicate secret values and must never be stored in config JSON.
SECRET_KEY_NAMES: set[str] = {
    "api_key",
    "apikey",
    "api_token",
    "access_token",
    "auth_token",
    "token",
    "password",
    "secret",
    "authorization",
}

PROFILE_CONFIG_FIELDS = {
    "permission_mode",
    "timeout_seconds",
    "working_directory_strategy",
    "allowed_env_names",
    "allowed_tools",
    "disallowed_tools",
}
DRAFT_CONFIG_FIELDS = {"timeout_seconds", "working_directory_strategy"}

# Shell metacharacters that indicate command injection attempts in string fields.
SHELL_METACHAR_PATTERN = re.compile(r"[;|&`$]\s*\S|&&|\|\||>\s|<\s|rm\s+-rf")

VALID_PERMISSION_MODES = {"default", "acceptEdits", "plan"}

# Version constraint: comma-separated semver comparisons, e.g. ">=1.0.0,<2.0.0".
_VERSION_CONSTRAINT_PATTERN = re.compile(
    r"^\s*(>=|<=|>|<|==|=|!=)?\s*\d+(\.\d+){0,2}"
    r"(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?"
    r"\s*(,\s*(>=|<=|>|<|==|=|!=)?\s*\d+(\.\d+){0,2}(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?\s*)*$"
)


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"


class Diagnostic(BaseModel):
    code: str
    field: str
    message: str


def validate_harness_type(harness_type: str) -> list[Diagnostic]:
    if harness_type not in ALLOWED_HARNESS_TYPES:
        return [
            Diagnostic(
                code="unsupported_harness",
                field="harness_type",
                message=f"harness_type '{harness_type}' is not supported; "
                f"v0.5 only allows {sorted(ALLOWED_HARNESS_TYPES)}",
            )
        ]
    return []


def _is_denylisted(name: str) -> bool:
    if name in ENV_DENYLIST:
        return True
    return any(name.startswith(p) for p in ENV_DENYLIST_PREFIXES)


def _is_allowlisted(name: str) -> bool:
    if name in ENV_ALLOWLIST:
        return True
    return any(name.startswith(p) for p in ENV_ALLOWLIST_PREFIXES)


def validate_env_names(names: list[str]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for name in names:
        if _is_denylisted(name):
            diags.append(
                Diagnostic(
                    code="env_denied",
                    field="allowed_env_names",
                    message=f"environment variable '{name}' is denylisted and "
                    "cannot be configured; denylist takes precedence",
                )
            )
        elif not _is_allowlisted(name):
            diags.append(
                Diagnostic(
                    code="env_not_allowlisted",
                    field="allowed_env_names",
                    message=f"environment variable '{name}' is not in the platform allowlist",
                )
            )
    return diags


def _check_secret_keys(config: object, prefix: str = "") -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if isinstance(config, list):
        for index, value in enumerate(config):
            diags.extend(_check_secret_keys(value, f"{prefix}[{index}]"))
        return diags
    if not isinstance(config, dict):
        return diags
    for raw_key, value in config.items():
        key = str(raw_key)
        field = f"{prefix}.{key}" if prefix else key
        normalized_key = re.sub(r"[-\s]+", "_", key.lower())
        if normalized_key in SECRET_KEY_NAMES:
            diags.append(
                Diagnostic(
                    code="secret_value_forbidden",
                    field=field,
                    message=f"field '{key}' looks like a secret; secrets must not be "
                    "stored in config",
                )
            )
        if normalized_key in {"header", "headers"} and value not in ({}, [], None):
            diags.append(
                Diagnostic(
                    code="header_value_forbidden",
                    field=field,
                    message="arbitrary Header values cannot be stored in config",
                )
            )
        diags.extend(_check_secret_keys(value, field))
    return diags


def _check_shell_strings(config: object, prefix: str = "") -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if isinstance(config, list):
        for index, value in enumerate(config):
            field = f"{prefix}[{index}]"
            if isinstance(value, str) and SHELL_METACHAR_PATTERN.search(value):
                diags.append(
                    Diagnostic(
                        code="shell_metachar_forbidden",
                        field=field,
                        message="config contains shell metacharacters; shell strings are not allowed",
                    )
                )
            else:
                diags.extend(_check_shell_strings(value, field))
        return diags
    if not isinstance(config, dict):
        return diags
    for raw_key, value in config.items():
        key = str(raw_key)
        field = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str) and SHELL_METACHAR_PATTERN.search(value):
            diags.append(
                Diagnostic(
                    code="shell_metachar_forbidden",
                    field=field,
                    message=f"field '{key}' contains shell metacharacters; "
                    "shell strings are not allowed",
                )
            )
        else:
            diags.extend(_check_shell_strings(value, field))
    return diags


def _validate_string_list(config: dict[str, Any], field: str) -> list[Diagnostic]:
    value = config.get(field)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return [
            Diagnostic(
                code="invalid_string_list",
                field=field,
                message=f"{field} must be a list of strings",
            )
        ]
    if len(set(value)) != len(value):
        return [
            Diagnostic(
                code="duplicate_list_item",
                field=field,
                message=f"{field} must not contain duplicate values",
            )
        ]
    return []


def validate_config(
    config: dict[str, Any], *, is_profile: bool = True
) -> list[Diagnostic]:
    """Validate a non-sensitive structured config (HarnessProfile or AgentDraft).

    Rejects bypassPermissions, unknown permission modes, secret-named keys,
    shell metacharacter strings, invalid timeouts, and denylisted/non-allowlisted
    env names. Returns a list of Diagnostic (empty == valid).
    """
    diags: list[Diagnostic] = []
    allowed_fields = PROFILE_CONFIG_FIELDS if is_profile else DRAFT_CONFIG_FIELDS
    for key in config:
        if key not in allowed_fields:
            diags.append(
                Diagnostic(
                    code="unknown_config_field",
                    field=str(key),
                    message=f"config field '{key}' is not supported by this schema",
                )
            )
    diags.extend(_check_secret_keys(config))
    diags.extend(_check_shell_strings(config))

    permission_mode = config.get("permission_mode")
    if permission_mode is not None:
        if permission_mode == "bypassPermissions":
            diags.append(
                Diagnostic(
                    code="bypass_permissions_forbidden",
                    field="permission_mode",
                    message="bypassPermissions is forbidden and cannot be saved",
                )
            )
        elif permission_mode not in VALID_PERMISSION_MODES:
            diags.append(
                Diagnostic(
                    code="unknown_permission_mode",
                    field="permission_mode",
                    message=f"unknown permission_mode '{permission_mode}'",
                )
            )

    timeout = config.get("timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int) or timeout <= 0:
            diags.append(
                Diagnostic(
                    code="timeout_invalid",
                    field="timeout_seconds",
                    message="timeout_seconds must be a positive integer",
                )
            )
        elif timeout > MAX_TIMEOUT_SECONDS:
            diags.append(
                Diagnostic(
                    code="timeout_exceeds_max",
                    field="timeout_seconds",
                    message=f"timeout_seconds exceeds platform max {MAX_TIMEOUT_SECONDS}",
                )
            )

    working_dir_strategy = config.get("working_directory_strategy")
    if working_dir_strategy is not None and working_dir_strategy not in {
        "inherit",
        "require_root",
    }:
        diags.append(
            Diagnostic(
                code="invalid_working_directory_strategy",
                field="working_directory_strategy",
                message="working_directory_strategy must be 'inherit' or 'require_root'",
            )
        )

    env_names = config.get("allowed_env_names")
    if env_names is not None:
        if not isinstance(env_names, list) or any(
            not isinstance(name, str) for name in env_names
        ):
            diags.append(
                Diagnostic(
                    code="invalid_env_names",
                    field="allowed_env_names",
                    message="allowed_env_names must be a list of strings",
                )
            )
        else:
            diags.extend(validate_env_names(env_names))

    for field in ("allowed_tools", "disallowed_tools"):
        diags.extend(_validate_string_list(config, field))
    allowed_tools = config.get("allowed_tools")
    disallowed_tools = config.get("disallowed_tools")
    if isinstance(allowed_tools, list) and isinstance(disallowed_tools, list):
        overlap = sorted(set(allowed_tools) & set(disallowed_tools))
        if overlap:
            diags.append(
                Diagnostic(
                    code="tool_policy_conflict",
                    field="allowed_tools",
                    message=f"tools cannot be both allowed and disallowed: {overlap}",
                )
            )

    return diags


def validate_version_constraint(expr: str) -> list[Diagnostic]:
    if not expr or not _VERSION_CONSTRAINT_PATTERN.match(expr.strip()):
        return [
            Diagnostic(
                code="invalid_version_constraint",
                field="version_constraint",
                message="version constraint must be comma-separated semver comparisons "
                "(e.g. '>=1.0.0,<2.0.0')",
            )
        ]
    return []


# Parses a single constraint clause like ">=1.0.0", "==1.5.0", "1.0.0" (bare ==).
_CLAUSE_PATTERN = re.compile(r"^\s*(>=|<=|==|!=|>|<|=)?\s*(\S+)\s*$")


def evaluate_version_constraint(reported_version: str, constraint: str) -> bool:
    """Return True if `reported_version` satisfies ALL clauses in `constraint`.

    `constraint` is a comma-separated list of comparisons, e.g. ">=1.0.0,<2.0.0".
    A bare version with no operator is treated as `==` (exact match).

    An empty constraint is treated as satisfied (no restriction). If the
    `reported_version` or any clause version is not parseable, the function
    returns False (cannot prove compatibility).
    """
    if not constraint or not constraint.strip():
        return True
    try:
        reported = Version(reported_version)
    except InvalidVersion:
        return False

    for raw_clause in constraint.split(","):
        clause = raw_clause.strip()
        if not clause:
            continue
        match = _CLAUSE_PATTERN.match(clause)
        if match is None:
            return False
        op = match.group(1) or "=="
        # Normalize a lone "=" to "==".
        if op == "=":
            op = "=="
        try:
            target = Version(match.group(2))
        except InvalidVersion:
            return False
        if op == ">=" and not (reported >= target):
            return False
        if op == "<=" and not (reported <= target):
            return False
        if op == ">" and not (reported > target):
            return False
        if op == "<" and not (reported < target):
            return False
        if op == "==" and not (reported == target):
            return False
        if op == "!=" and not (reported != target):
            return False
    return True


HARNESS_CATALOG: list[dict[str, Any]] = [
    {
        "type": "claude_code",
        "config_schema_version": "1.0",
        "supported": True,
        "description": "Claude Code CLI / Claude Agent SDK harness",
        "fields": [
            {
                "name": "permission_mode",
                "type": "enum",
                "allowed": sorted(VALID_PERMISSION_MODES),
            },
            {"name": "timeout_seconds", "type": "integer", "max": MAX_TIMEOUT_SECONDS},
            {
                "name": "working_directory_strategy",
                "type": "enum",
                "allowed": ["inherit", "require_root"],
            },
            {
                "name": "allowed_env_names",
                "type": "string_list",
                "allowlist": sorted(ENV_ALLOWLIST),
            },
            {"name": "allowed_tools", "type": "string_list"},
            {"name": "disallowed_tools", "type": "string_list"},
        ],
    }
]


def environment_catalog() -> dict[str, list[str]]:
    return {
        "allowlist": sorted(ENV_ALLOWLIST),
        "reserved": sorted(ENV_RESERVED),
        "denylist": sorted(ENV_DENYLIST),
    }
