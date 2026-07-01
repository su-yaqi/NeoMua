from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from runtime_worker.events import normalize_message


@dataclass
class RunCommand:
    prompt: str
    model: str
    permission_mode: str = "default"
    tools: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    sdk_session_id: str | None = None

    def __post_init__(self) -> None:
        if self.permission_mode == "bypassPermissions":
            raise ValueError("bypassPermissions is not allowed")


class AgentShell:
    @staticmethod
    def build_options(command: RunCommand) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=command.model,
            tools=command.tools,
            allowed_tools=command.allowed_tools,
            disallowed_tools=command.disallowed_tools,
            permission_mode=command.permission_mode,  # type: ignore[arg-type]
            cwd=command.cwd,
            env=command.env,
            resume=command.sdk_session_id,
        )

    async def run_session(self, command: RunCommand) -> AsyncIterator[dict]:
        sequence = 0
        async with ClaudeSDKClient(options=self.build_options(command)) as client:
            await client.query(command.prompt)
            async for message in client.receive_response():
                sequence += 1
                yield normalize_message(message, sequence)
