from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from runtime_worker.events import normalize_messages


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
    start_sequence: int = 0

    def __post_init__(self) -> None:
        if self.permission_mode == "bypassPermissions":
            raise ValueError("bypassPermissions is not allowed")


class AgentShell:
    def __init__(self) -> None:
        self._clients: dict[str, ClaudeSDKClient] = {}

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

    async def run_session(
        self, command: RunCommand, *, task_id: str | None = None
    ) -> AsyncIterator[dict]:
        sequence = command.start_sequence
        async with ClaudeSDKClient(options=self.build_options(command)) as client:
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
