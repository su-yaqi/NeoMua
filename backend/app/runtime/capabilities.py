from typing import Any

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HarnessCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cli_version: str = Field(min_length=1, max_length=64)
    sdk_version: str | None = Field(default=None, min_length=1, max_length=64)
    harness_version: str | None = Field(default=None, min_length=1, max_length=64)
    adapter_version: str | None = Field(default=None, min_length=1, max_length=64)
    builtin_tools: list[str] | None = None
    supports_tool_filters: bool | None = None
    supports_per_tool_approval: bool | None = None
    supports_mcp_injection: bool | None = None
    permission_modes: list[str] | None = None
    executable: str | None = None

    @field_validator("cli_version", "sdk_version", "harness_version", "adapter_version")
    @classmethod
    def require_parseable_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            Version(value)
        except InvalidVersion as exc:
            raise ValueError("must be a parseable version") from exc
        return value


class HarnessCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claude_agent_sdk: HarnessCapability | None = None
    claude_code: HarnessCapability | None = None
    codex: HarnessCapability | None = None
    mcp_executables: list[str] | None = None


def validate_harness_capabilities(value: object) -> dict[str, Any]:
    parsed = HarnessCapabilities.model_validate(value)
    if parsed.mcp_executables is not None and len(parsed.mcp_executables) != len(
        set(parsed.mcp_executables)
    ):
        raise ValueError("mcp_executables must not contain duplicates")
    for capability in (
        parsed.claude_agent_sdk,
        parsed.claude_code,
        parsed.codex,
    ):
        if (
            capability is not None
            and capability.builtin_tools is not None
            and len(capability.builtin_tools) != len(set(capability.builtin_tools))
        ):
            raise ValueError("builtin_tools must not contain duplicates")
    return parsed.model_dump(exclude_none=True)
