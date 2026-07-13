from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from runtime_worker.events import normalize_messages
from runtime_worker.permissions import permission_mode_for_sdk, validate_permission_mode


@dataclass
class RunCommand:
    prompt: str
    model: str
    system_prompt: str | None = None
    permission_mode: str = "default"
    tools: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    sdk_session_id: str | None = None
    start_sequence: int = 0
    timeout_seconds: int = 3600
    task_revision: int = 1
    require_approval_tools: list[str] = field(default_factory=list)
    add_dirs: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    approval_callback: (
        Callable[[str, int, str, dict[str, Any]], Awaitable[bool]] | None
    ) = None

    def __post_init__(self) -> None:
        validate_permission_mode(self.permission_mode)
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")


class AgentShell:
    def __init__(self) -> None:
        self._clients: dict[str, ClaudeSDKClient] = {}

    @staticmethod
    def build_options(
        command: RunCommand, task_id: str | None = None
    ) -> ClaudeAgentOptions:
        async def can_use_tool(
            tool_name: str, tool_input: dict[str, Any], _context: Any
        ) -> PermissionResultAllow | PermissionResultDeny:
            if tool_name not in command.require_approval_tools:
                return PermissionResultAllow()
            if command.approval_callback is None or task_id is None:
                return PermissionResultDeny(
                    message="Tool approval channel is unavailable"
                )
            approved = await command.approval_callback(
                task_id, command.task_revision, tool_name, tool_input
            )
            return (
                PermissionResultAllow()
                if approved
                else PermissionResultDeny(message="Tool Call was denied or expired")
            )

        return ClaudeAgentOptions(
            model=command.model,
            system_prompt=command.system_prompt,
            tools=command.tools,
            allowed_tools=command.allowed_tools,
            disallowed_tools=command.disallowed_tools,
            permission_mode=permission_mode_for_sdk(command.permission_mode),
            cwd=command.cwd,
            env=command.env,
            resume=command.sdk_session_id,
            can_use_tool=can_use_tool,
            add_dirs=[Path(value) for value in command.add_dirs],
            skills=command.skills,
            mcp_servers=command.mcp_servers,
            strict_mcp_config=True,
        )

    async def run_session(
        self, command: RunCommand, *, task_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        sequence = command.start_sequence
        async with ClaudeSDKClient(
            options=self.build_options(command, task_id)
        ) as client:
            if task_id:
                self._clients[task_id] = client
            try:
                await client.query(command.prompt)
                async for message in client.receive_response():
                    events = normalize_messages(message, sequence + 1)
                    for event in events:
                        sequence = event["sequence"]
                        yield event
            finally:
                if task_id:
                    self._clients.pop(task_id, None)

    async def interrupt(self, task_id: str) -> bool:
        client = self._clients.get(task_id)
        if client is None:
            return False
        await client.interrupt()
        return True
