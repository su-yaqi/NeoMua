import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from runtime_worker.events import normalize_messages
from runtime_worker.permissions import permission_mode_for_sdk, validate_permission_mode


@dataclass
class RunCommand:
    prompt: str
    model: str
    engine_type: str = "claude_code"
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
    roundtable_participants: list[dict[str, str]] = field(default_factory=list)
    approval_callback: (
        Callable[[str, int, str, dict[str, Any]], Awaitable[bool]] | None
    ) = None
    delegation_callback: (
        Callable[[str, int, str, str], Awaitable[dict[str, Any]]] | None
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

        mcp_servers = dict(command.mcp_servers)
        allowed_tools = list(command.allowed_tools)
        if command.roundtable_participants:
            participant_ids = {
                str(item["conversation_agent_id"])
                for item in command.roundtable_participants
            }

            @tool(
                "delegate_to_collaborator",
                (
                    "Delegate one explicit subproblem to a collaborator selected for "
                    "this roundtable and wait for that collaborator's complete result."
                ),
                {
                    "conversation_agent_id": str,
                    "task": str,
                },
            )
            async def delegate_to_collaborator(args: dict[str, Any]) -> dict[str, Any]:
                participant_id = str(args.get("conversation_agent_id", ""))
                delegated_task = str(args.get("task", "")).strip()
                if participant_id not in participant_ids:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "The requested collaborator is not part of this roundtable.",
                            }
                        ],
                        "is_error": True,
                    }
                if not delegated_task:
                    return {
                        "content": [
                            {"type": "text", "text": "Delegated task is empty."}
                        ],
                        "is_error": True,
                    }
                if command.delegation_callback is None or task_id is None:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Roundtable delegation channel is unavailable.",
                            }
                        ],
                        "is_error": True,
                    }
                result = await command.delegation_callback(
                    task_id,
                    command.task_revision,
                    participant_id,
                    delegated_task,
                )
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                result, ensure_ascii=False, sort_keys=True
                            ),
                        }
                    ],
                    "is_error": result.get("status") != "completed",
                }

            mcp_servers["neomua-roundtable"] = create_sdk_mcp_server(
                "neomua-roundtable", tools=[delegate_to_collaborator]
            )
            qualified_name = "mcp__neomua-roundtable__delegate_to_collaborator"
            if qualified_name not in allowed_tools:
                allowed_tools.append(qualified_name)

        return ClaudeAgentOptions(
            model=command.model,
            system_prompt=command.system_prompt,
            tools=command.tools,
            allowed_tools=allowed_tools,
            disallowed_tools=command.disallowed_tools,
            permission_mode=permission_mode_for_sdk(command.permission_mode),
            cwd=command.cwd,
            env=command.env,
            resume=command.sdk_session_id,
            can_use_tool=can_use_tool,
            add_dirs=[Path(value) for value in command.add_dirs],
            skills=command.skills,
            mcp_servers=mcp_servers,
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


class CodexShell:
    """Built-in Codex CLI adapter using argv execution and JSONL events."""

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("codex")
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @staticmethod
    def build_argv(command: RunCommand, executable: str) -> list[str]:
        if command.tools or command.allowed_tools or command.disallowed_tools:
            raise ValueError("adapter_contract_unsupported: Codex tool filters")
        if command.require_approval_tools:
            raise ValueError("adapter_contract_unsupported: Codex per-tool approval")
        if command.mcp_servers:
            raise ValueError("adapter_contract_unsupported: Codex MCP injection")
        sandbox = {
            "default": "workspace-write",
            "acceptEdits": "workspace-write",
            "plan": "read-only",
        }.get(command.permission_mode)
        if sandbox is None:
            raise ValueError(
                f"adapter_contract_unsupported: permission mode {command.permission_mode}"
            )
        argv = [
            executable,
            "exec",
            "--json",
            "--ephemeral",
            "--model",
            command.model,
            "--sandbox",
            sandbox,
        ]
        if command.cwd:
            argv.extend(["--cd", command.cwd])
        for directory in command.add_dirs:
            argv.extend(["--add-dir", directory])
        if command.system_prompt:
            argv.extend(
                ["--config", f"developer_instructions={json.dumps(command.system_prompt)}"]
            )
        argv.append(command.prompt)
        return argv

    async def run_session(
        self, command: RunCommand, *, task_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.executable:
            raise ValueError("codex executable is not installed")
        argv = self.build_argv(command, self.executable)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=command.cwd,
            env={**os.environ, **command.env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if task_id:
            self._processes[task_id] = process
        sequence = command.start_sequence
        try:
            assert process.stdout is not None
            async for raw_line in process.stdout:
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                normalized: dict[str, Any] | None = None
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                if event_type == "item.completed" and item.get("type") == "agent_message":
                    normalized = {
                        "event_type": "assistant_message",
                        "payload": {"text": str(item.get("text", ""))},
                    }
                elif event_type in {"turn.failed", "error"}:
                    normalized = {
                        "event_type": "error",
                        "payload": {
                            "code": "codex_execution_failed",
                            "message": str(event.get("message") or event.get("error") or "Codex failed"),
                        },
                    }
                elif event_type == "turn.completed":
                    normalized = {
                        "event_type": "result",
                        "payload": {"usage": event.get("usage", {})},
                    }
                if normalized is not None:
                    sequence += 1
                    yield {"sequence": sequence, **normalized}
            return_code = await process.wait()
            if return_code != 0:
                assert process.stderr is not None
                stderr = (await process.stderr.read()).decode(errors="replace")[-2048:]
                sequence += 1
                yield {
                    "sequence": sequence,
                    "event_type": "error",
                    "payload": {
                        "code": "codex_process_failed",
                        "exit_code": return_code,
                        "message": stderr,
                    },
                }
        finally:
            if task_id:
                self._processes.pop(task_id, None)

    async def interrupt(self, task_id: str) -> bool:
        process = self._processes.get(task_id)
        if process is None or process.returncode is not None:
            return False
        process.terminate()
        return True


class EngineShell:
    def __init__(self) -> None:
        self.adapters = {
            "claude_code": AgentShell(),
            "codex": CodexShell(),
        }
        self._task_engines: dict[str, str] = {}

    async def run_session(
        self, command: RunCommand, *, task_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        adapter = self.adapters.get(command.engine_type)
        if adapter is None:
            raise ValueError(f"unknown Runtime engine: {command.engine_type}")
        if task_id:
            self._task_engines[task_id] = command.engine_type
        try:
            async for event in adapter.run_session(command, task_id=task_id):
                yield event
        finally:
            if task_id:
                self._task_engines.pop(task_id, None)

    async def interrupt(self, task_id: str) -> bool:
        engine = self._task_engines.get(task_id)
        adapter = self.adapters.get(engine) if engine else None
        return await adapter.interrupt(task_id) if adapter else False
