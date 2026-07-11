from typing import Literal, cast

from claude_agent_sdk.types import PermissionMode as SDKPermissionMode

PermissionMode = Literal["default", "acceptEdits", "plan", "dontAsk"]
SUPPORTED_PERMISSION_MODES: frozenset[str] = frozenset(
    {"default", "acceptEdits", "plan", "dontAsk"}
)


def validate_permission_mode(value: str) -> PermissionMode:
    if value not in SUPPORTED_PERMISSION_MODES:
        raise ValueError(f"permission_mode is not supported: {value}")
    return cast(PermissionMode, value)


def permission_mode_for_sdk(value: str) -> SDKPermissionMode:
    """Return the SDK's annotated literal after the shared allowlist check."""
    validate_permission_mode(value)
    return cast(SDKPermissionMode, value)
