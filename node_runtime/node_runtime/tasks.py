import asyncio
import hashlib
import json
from enum import Enum
from typing import Any, Protocol

from runtime_worker.agent_shell import EngineShell, RunCommand
from runtime_worker.mcp_manager import McpRuntimeManager
from runtime_worker.permissions import validate_permission_mode
from runtime_worker.release_store import AgentReleaseStore
from runtime_worker.runtime_configuration import RuntimeConfigurationStore
from runtime_worker.skill_store import SkillStore
from workflow_runtime.executor import execute_runtime_job

from node_runtime.model_route import ModelRouteStore, build_route_env
from node_runtime.protocol import Envelope, envelope
from node_runtime.secrets import read_node_secret
from node_runtime.spool import EventSpool


class DispatchDecision(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    STALE = "stale"
    CONFLICT = "conflict"


class TaskExecutor(Protocol):
    def validate(self, command: dict) -> None: ...
    def start(self, task_id: str, revision: int, command: dict) -> None: ...


class TaskDispatcher:
    def __init__(self, spool: EventSpool, executor: TaskExecutor) -> None:
        self.spool = spool
        self.executor = executor

    def accept(self, command: dict, *, start: bool = True) -> DispatchDecision:
        task_id = str(command["task_id"])
        revision = int(command["revision"])
        validate = getattr(self.executor, "validate", None)
        if validate:
            validate(command)
        result = DispatchDecision(
            self.spool.record_dispatch(task_id, revision, command["snapshot"])
        )
        if result == DispatchDecision.ACCEPTED and start:
            self.executor.start(task_id, revision, command)
        return result


class NodeTaskExecutor:
    def __init__(
        self,
        spool: EventSpool,
        route_store: ModelRouteStore,
        outbox: asyncio.Queue[Envelope],
        node_id: str,
        *,
        shell: EngineShell | None = None,
        release_store: AgentReleaseStore | None = None,
        skill_store: SkillStore | None = None,
        runtime_configuration_store: RuntimeConfigurationStore | None = None,
        approval_callback=None,
        delegation_callback=None,
        mcp_manager: McpRuntimeManager | None = None,
    ) -> None:
        self.spool = spool
        self.route_store = route_store
        self.outbox = outbox
        self.node_id = node_id
        self.shell = shell or EngineShell()
        self.running: dict[str, asyncio.Task] = {}
        self.cancelling: set[str] = set()
        self.release_store = release_store
        self.skill_store = skill_store
        self.runtime_configuration_store = runtime_configuration_store
        self.approval_callback = approval_callback
        self.delegation_callback = delegation_callback
        self.mcp_manager = mcp_manager or McpRuntimeManager()

    def validate(self, command: dict) -> None:
        snapshot = command["snapshot"]
        if snapshot.get("runtime_instance_id"):
            if self.runtime_configuration_store is None:
                raise ValueError("runtime_configuration_store_unavailable")
            configuration = self.runtime_configuration_store.get(
                str(snapshot["runtime_instance_id"])
            )
            if configuration is None:
                raise ValueError("runtime_configuration_not_applied_locally")
            configuration.validate_task_snapshot(snapshot)
        if snapshot.get("agent_release_id"):
            if self.release_store is None:
                raise ValueError(
                    "release_not_active: Agent Release store is unavailable"
                )
            self.release_store.verify_installed(
                str(snapshot["agent_id"]),
                str(snapshot["agent_release_id"]),
                str(snapshot["resolved_spec_digest"]),
            )
        if snapshot.get("resolved_spec_schema_version") in {"1.1", "2.0"}:
            if self.skill_store is None:
                raise ValueError("skill_store_unavailable")
            self.skill_store.usage_evidence(
                str(
                    snapshot.get("runtime_instance_id")
                    or snapshot["runtime_profile_id"]
                ),
                list(snapshot.get("skills", [])),
            )
        validate_permission_mode(snapshot.get("permission_mode", "default"))
        cwd = snapshot.get("working_directory")
        roots = snapshot.get("allowed_working_roots", [])
        if cwd and not any(
            cwd == root or cwd.startswith(f"{root.rstrip('/')}/") for root in roots
        ):
            raise ValueError("working directory is outside snapshot allowlist")
        if not command.get("requires_model_preparation"):
            build_route_env(command["route"], snapshot, self.route_store)

    def start(self, task_id: str, revision: int, command: dict) -> None:
        if task_id in self.running:
            return
        self.running[task_id] = asyncio.create_task(
            self._run_with_lease(task_id, revision, command)
        )

    def model_evidence(self, command: dict) -> dict[str, Any]:
        snapshot = command["snapshot"]
        if self.runtime_configuration_store is None:
            raise ValueError("runtime_configuration_store_unavailable")
        configuration = self.runtime_configuration_store.get(
            str(snapshot["runtime_instance_id"])
        )
        if configuration is None:
            raise ValueError("runtime_configuration_not_applied_locally")
        return configuration.validate_task_snapshot(snapshot)

    def prepare_skill_binding(self, command: dict) -> list[dict]:
        snapshot = command["snapshot"]
        if snapshot.get("resolved_spec_schema_version") not in {"1.1", "2.0"}:
            return []
        if self.skill_store is None:
            raise ValueError("skill_store_unavailable")
        task_id = str(command["task_id"])
        path, evidence = self.skill_store.bind_task(
            task_id,
            str(snapshot.get("runtime_instance_id") or snapshot["runtime_profile_id"]),
            list(snapshot.get("skills", [])),
        )
        command["_skill_binding_path"] = str(path)
        return evidence

    async def _run_with_lease(self, task_id: str, revision: int, command: dict) -> None:
        lease = asyncio.create_task(self._lease_loop(task_id, revision))
        try:
            await self._run(task_id, revision, command)
        finally:
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
            self.running.pop(task_id, None)

    async def _lease_loop(self, task_id: str, revision: int) -> None:
        while True:
            await asyncio.sleep(60)
            await self.outbox.put(
                envelope(
                    "lease_renewed",
                    self.node_id,
                    {"task_id": task_id, "revision": revision},
                )
            )

    async def _run(self, task_id: str, revision: int, command: dict) -> None:
        snapshot = command["snapshot"]
        sequence = 0
        try:
            configuration = (
                self.runtime_configuration_store.get(
                    str(snapshot["runtime_instance_id"])
                )
                if self.runtime_configuration_store
                and snapshot.get("runtime_instance_id")
                else None
            )
            env = (
                configuration.task_environment(
                    self.runtime_configuration_store.source_environment
                )
                if configuration and self.runtime_configuration_store
                else {}
            )
            env.update(build_route_env(command["route"], snapshot, self.route_store))
            release_store = self.release_store
            release_dirs: list[str] = []
            if snapshot.get("agent_release_id"):
                if release_store is None:
                    raise ValueError("agent_release_store_unavailable")
                release_dirs.append(
                    str(
                        release_store.verify_installed(
                            str(snapshot["agent_id"]),
                            str(snapshot["agent_release_id"]),
                            str(snapshot["resolved_spec_digest"]),
                        )
                    )
                )
            run_command = RunCommand(
                engine_type=snapshot.get("engine_type", "claude_code"),
                executable=configuration.execution_path() if configuration else None,
                prompt=command["prompt"],
                model=snapshot.get("engine_model_id", snapshot.get("model_id")),
                system_prompt=snapshot.get("system_prompt"),
                permission_mode=snapshot.get("permission_mode", "default"),
                tools=snapshot.get("tools", []),
                allowed_tools=snapshot.get("allowed_tools", []),
                disallowed_tools=snapshot.get("disallowed_tools", []),
                cwd=snapshot.get("working_directory"),
                env=env,
                start_sequence=int(command.get("event_sequence_start", 1)),
                timeout_seconds=int(snapshot.get("timeout_seconds", 3600)),
                task_revision=revision,
                require_approval_tools=snapshot.get("require_approval_tools", []),
                approval_callback=self.approval_callback,
                delegation_callback=self.delegation_callback,
                add_dirs=[
                    *release_dirs,
                    *(
                        [str(command["_skill_binding_path"])]
                        if command.get("_skill_binding_path")
                        else []
                    ),
                ],
                skills=[str(item["slug"]) for item in snapshot.get("skills", [])],
                roundtable_participants=list(
                    snapshot.get("roundtable_participants", [])
                ),
            )
            mcp_runtime_configs = []
            for server in snapshot.get("mcp_servers", []):
                handles = [
                    item
                    for item in server.get("secret_handles", [])
                    if item.get("runtime_instance_id")
                    == str(snapshot.get("runtime_instance_id"))
                    or item.get("runtime_profile_id")
                    == str(snapshot.get("runtime_profile_id"))
                ]
                if len(handles) != 1 or not handles[0].get("secret_ref"):
                    raise ValueError(f"mcp_target_not_ready: {server.get('slug')}")
                mcp_runtime_configs.append(
                    {
                        **server,
                        "secret_inputs": read_node_secret(
                            str(handles[0]["secret_ref"])
                        ),
                    }
                )
            run_command.mcp_servers = self.mcp_manager.execution_configs(
                mcp_runtime_configs
            )

            async def consume() -> None:
                nonlocal sequence
                async for event in self.shell.run_session(run_command, task_id=task_id):
                    sequence = int(event["sequence"])
                    if task_id in self.cancelling and event["event_type"] == "result":
                        event = {
                            "sequence": sequence,
                            "event_type": "status",
                            "payload": {
                                "state": "sdk_interrupted",
                                "sdk_terminal": event["payload"],
                            },
                        }
                    self.spool.append(
                        task_id, sequence, event["event_type"], event["payload"]
                    )
                    await self._queue_pending(task_id)

            execution = asyncio.create_task(consume())
            done, _ = await asyncio.wait(
                {execution}, timeout=run_command.timeout_seconds
            )
            if not done:
                await self.shell.interrupt(task_id)
                try:
                    await asyncio.wait_for(asyncio.shield(execution), timeout=10)
                except TimeoutError:
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                sequence += 1
                self.spool.append(
                    task_id,
                    sequence,
                    "error",
                    {
                        "code": "task_timeout",
                        "timeout_seconds": run_command.timeout_seconds,
                    },
                )
                await self._queue_pending(task_id)
            else:
                execution.result()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            sequence += 1
            payload = {"code": "executor_error", "message": str(exc)}
            self.spool.append(task_id, sequence, "error", payload)
            await self._queue_pending(task_id)
        finally:
            if self.skill_store is not None:
                self.skill_store.remove_task_binding(task_id)
            self.spool.mark_dispatch_state(task_id, "terminal")

    async def _queue_pending(self, task_id: str) -> None:
        pending = self.spool.pending(task_id)
        if not pending:
            return
        await self.outbox.put(
            envelope(
                "task_events",
                self.node_id,
                {
                    "task_id": task_id,
                    "events": [
                        {
                            "sequence": item.sequence,
                            "event_type": item.event_type,
                            "payload": item.payload,
                        }
                        for item in pending
                    ],
                },
            )
        )

    async def cancel(self, task_id: str) -> bool:
        task = self.running.get(task_id)
        if task is None:
            return False
        self.cancelling.add(task_id)
        await self.shell.interrupt(task_id)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self.cancelling.discard(task_id)
        return True


class NodeTaskController:
    def __init__(
        self,
        node_id: str,
        spool: EventSpool,
        route_store: ModelRouteStore,
        *,
        shell: EngineShell | None = None,
        release_store: AgentReleaseStore | None = None,
        skill_store: SkillStore | None = None,
        runtime_configuration_store: RuntimeConfigurationStore | None = None,
    ) -> None:
        self.node_id = node_id
        self.spool = spool
        self.outbox: asyncio.Queue[Envelope] = asyncio.Queue()
        self.approval_waiters: dict[str, asyncio.Future[bool]] = {}
        self.delegation_waiters: dict[str, asyncio.Future[dict]] = {}
        self.runtime_jobs: dict[str, asyncio.Task] = {}
        self.pending_tasks: dict[str, tuple[int, dict]] = {}
        self.executor = NodeTaskExecutor(
            spool,
            route_store,
            self.outbox,
            node_id,
            shell=shell,
            release_store=release_store,
            skill_store=skill_store,
            runtime_configuration_store=runtime_configuration_store,
            approval_callback=self._request_approval,
            delegation_callback=self._request_delegation,
        )
        self.dispatcher = TaskDispatcher(spool, self.executor)

    async def handle(self, message: Envelope) -> list[Envelope]:
        if message.type == "runtime_job_dispatch":
            job_id = str(message.payload["job_id"])
            revision = int(message.payload["revision"])
            decision = self.spool.record_runtime_job_dispatch(
                job_id, revision, dict(message.payload)
            )
            if decision not in {"accepted", "duplicate"}:
                return [
                    envelope(
                        "runtime_job_rejected",
                        self.node_id,
                        {
                            "job_id": job_id,
                            "revision": revision,
                            "reason": decision,
                        },
                    )
                ]
            if decision == "accepted" and job_id not in self.runtime_jobs:
                self.runtime_jobs[job_id] = asyncio.create_task(
                    self._run_runtime_job(job_id, revision, message.payload)
                )
            responses = [
                envelope(
                    "runtime_job_accepted",
                    self.node_id,
                    {
                        "job_id": job_id,
                        "revision": revision,
                        "duplicate": decision == "duplicate",
                    },
                )
            ]
            stored_result = self.spool.runtime_job_result(job_id, revision)
            if decision == "duplicate" and stored_result is not None:
                responses.append(
                    envelope("runtime_job_result", self.node_id, stored_result)
                )
            return responses
        if message.type == "task_dispatch":
            try:
                decision = self.dispatcher.accept(message.payload, start=False)
                if decision == DispatchDecision.ACCEPTED:
                    evidence = self.executor.prepare_skill_binding(message.payload)
                elif (
                    decision == DispatchDecision.DUPLICATE
                    and self.executor.skill_store is not None
                    and message.payload["snapshot"].get("resolved_spec_schema_version")
                    in {"1.1", "2.0"}
                ):
                    evidence = self.executor.skill_store.usage_evidence(
                        str(
                            message.payload["snapshot"].get("runtime_instance_id")
                            or message.payload["snapshot"]["runtime_profile_id"]
                        ),
                        list(message.payload["snapshot"].get("skills", [])),
                    )
                else:
                    evidence = []
                model_evidence = (
                    self.executor.model_evidence(message.payload)
                    if message.payload.get("requires_model_preparation")
                    else None
                )
            except Exception as exc:
                self.spool.mark_dispatch_state(
                    str(message.payload.get("task_id", "")), "terminal"
                )
                if self.executor.skill_store is not None:
                    self.executor.skill_store.remove_task_binding(
                        str(message.payload.get("task_id", ""))
                    )
                return [
                    envelope(
                        "task_rejected",
                        self.node_id,
                        {"task_id": message.payload.get("task_id"), "reason": str(exc)},
                    )
                ]
            if decision in {DispatchDecision.ACCEPTED, DispatchDecision.DUPLICATE}:
                if decision == DispatchDecision.ACCEPTED:
                    self.pending_tasks[str(message.payload["task_id"])] = (
                        int(message.payload["revision"]),
                        message.payload,
                    )
                return [
                    envelope(
                        "task_accepted",
                        self.node_id,
                        {
                            "task_id": message.payload["task_id"],
                            "revision": message.payload["revision"],
                            "duplicate": decision == DispatchDecision.DUPLICATE,
                            "skill_evidence": evidence,
                            "model_evidence": model_evidence,
                        },
                    )
                ]
            if self.executor.skill_store is not None:
                self.executor.skill_store.remove_task_binding(
                    str(message.payload["task_id"])
                )
            return [
                envelope(
                    "task_rejected",
                    self.node_id,
                    {"task_id": message.payload["task_id"], "reason": decision.value},
                )
            ]
        if message.type == "task_accept_ack":
            task_id = str(message.payload["task_id"])
            pending = self.pending_tasks.pop(task_id, None)
            if pending is not None:
                revision, command = pending
                if message.payload.get("route") is not None:
                    command["route"] = message.payload["route"]
                self.executor.start(task_id, revision, command)
            return []
        if message.type == "task_accept_rejected":
            task_id = str(message.payload["task_id"])
            self.pending_tasks.pop(task_id, None)
            if self.executor.skill_store is not None:
                self.executor.skill_store.remove_task_binding(task_id)
            self.spool.mark_dispatch_state(task_id, "terminal")
            return []
        if message.type == "task_events_ack":
            self.spool.acknowledge(
                str(message.payload["task_id"]),
                int(message.payload["through_sequence"]),
            )
        elif message.type == "task_cancel":
            await self.executor.cancel(str(message.payload["task_id"]))
            return [
                envelope(
                    "task_cancelled",
                    self.node_id,
                    {
                        "task_id": message.payload["task_id"],
                        "revision": message.payload["revision"],
                    },
                )
            ]
        elif message.type == "tool_approval_decision":
            approval_id = str(message.payload["approval_id"])
            approval_key = str(message.payload["client_approval_key"])
            waiter = self.approval_waiters.get(approval_key)
            if waiter and not waiter.done():
                waiter.set_result(message.payload["status"] == "approved")
            return [
                envelope(
                    "tool_approval_decision_ack",
                    self.node_id,
                    {"approval_id": approval_id},
                )
            ]
        elif message.type == "agent_delegation_result":
            delegation_key = str(message.payload["client_delegation_key"])
            waiter = self.delegation_waiters.get(delegation_key)
            if waiter and not waiter.done():
                waiter.set_result(dict(message.payload["result"]))
            return [
                envelope(
                    "agent_delegation_result_ack",
                    self.node_id,
                    {
                        "delegation_id": message.payload["delegation_id"],
                        "client_delegation_key": delegation_key,
                    },
                )
            ]
        return []

    async def _run_runtime_job(self, job_id: str, revision: int, command: dict) -> None:
        lease = asyncio.create_task(self._runtime_job_lease(job_id, revision))
        try:
            try:
                result = await asyncio.to_thread(
                    execute_runtime_job,
                    str(command["kind"]),
                    dict(command["payload"]),
                )
                payload = {
                    "job_id": job_id,
                    "revision": revision,
                    "status": "succeeded",
                    "result": result,
                }
            except Exception as exc:
                payload = {
                    "job_id": job_id,
                    "revision": revision,
                    "status": (
                        "needs_manual_resolution"
                        if command.get("side_effecting")
                        else "failed"
                    ),
                    "error": {
                        "code": "runtime_job_execution_failed",
                        "message": str(exc),
                    },
                }
            self.spool.complete_runtime_job(job_id, revision, payload)
            await self.outbox.put(envelope("runtime_job_result", self.node_id, payload))
        finally:
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
            self.runtime_jobs.pop(job_id, None)

    async def _runtime_job_lease(self, job_id: str, revision: int) -> None:
        while True:
            await asyncio.sleep(60)
            await self.outbox.put(
                envelope(
                    "runtime_job_lease",
                    self.node_id,
                    {"job_id": job_id, "revision": revision},
                )
            )

    async def _request_approval(
        self, task_id: str, task_revision: int, tool_name: str, tool_input: dict
    ) -> bool:
        args_digest = hashlib.sha256(
            json.dumps(
                tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        approval_key = f"{task_id}:{task_revision}:{tool_name}:{args_digest}"
        loop = asyncio.get_running_loop()
        waiter = self.approval_waiters.setdefault(approval_key, loop.create_future())
        redacted = {
            key: "[REDACTED]"
            if key.lower()
            in {"api_key", "token", "password", "secret", "authorization"}
            else value
            for key, value in tool_input.items()
        }
        await self.outbox.put(
            envelope(
                "tool_approval_request",
                self.node_id,
                {
                    "client_approval_key": approval_key,
                    "task_id": task_id,
                    "task_revision": task_revision,
                    "tool_call_id": f"{tool_name}:{args_digest[:16]}",
                    "tool_qualified_name": tool_name,
                    "redacted_args": redacted,
                    "args_digest": args_digest,
                    "expires_in_seconds": 300,
                },
            )
        )
        try:
            return await asyncio.wait_for(waiter, timeout=305)
        except TimeoutError:
            return False
        finally:
            self.approval_waiters.pop(approval_key, None)

    async def _request_delegation(
        self,
        task_id: str,
        task_revision: int,
        target_conversation_agent_id: str,
        content: str,
    ) -> dict:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "task_id": task_id,
                    "revision": task_revision,
                    "target": target_conversation_agent_id,
                    "content": content,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        delegation_key = f"{task_id}:{task_revision}:{digest}"
        loop = asyncio.get_running_loop()
        waiter = self.delegation_waiters.setdefault(
            delegation_key, loop.create_future()
        )
        await self.outbox.put(
            envelope(
                "agent_delegation_request",
                self.node_id,
                {
                    "client_delegation_key": delegation_key,
                    "source_task_id": task_id,
                    "source_task_revision": task_revision,
                    "target_conversation_agent_id": target_conversation_agent_id,
                    "content": content,
                },
            )
        )
        try:
            return await waiter
        finally:
            self.delegation_waiters.pop(delegation_key, None)

    async def replay_pending(self) -> None:
        task_ids = sorted({event.task_id for event in self.spool.pending()})
        for task_id in task_ids:
            await self.executor._queue_pending(task_id)
