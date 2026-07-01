import asyncio
from enum import Enum
from typing import Protocol

from node_runtime.model_route import ModelRouteStore, build_route_env
from node_runtime.protocol import Envelope, envelope
from node_runtime.spool import EventSpool
from runtime_worker.agent_shell import AgentShell, RunCommand


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

    def accept(self, command: dict) -> DispatchDecision:
        task_id = str(command["task_id"])
        revision = int(command["revision"])
        validate = getattr(self.executor, "validate", None)
        if validate:
            validate(command)
        result = DispatchDecision(
            self.spool.record_dispatch(task_id, revision, command["snapshot"])
        )
        if result == DispatchDecision.ACCEPTED:
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
        shell: AgentShell | None = None,
    ) -> None:
        self.spool = spool
        self.route_store = route_store
        self.outbox = outbox
        self.node_id = node_id
        self.shell = shell or AgentShell()
        self.running: dict[str, asyncio.Task] = {}
        self.cancelling: set[str] = set()

    def validate(self, command: dict) -> None:
        snapshot = command["snapshot"]
        if snapshot.get("permission_mode") == "bypassPermissions":
            raise ValueError("bypassPermissions is not allowed")
        cwd = snapshot.get("working_directory")
        roots = snapshot.get("allowed_working_roots", [])
        if cwd and not any(cwd == root or cwd.startswith(f"{root.rstrip('/')}/") for root in roots):
            raise ValueError("working directory is outside snapshot allowlist")
        build_route_env(command["route"], snapshot, self.route_store)

    def start(self, task_id: str, revision: int, command: dict) -> None:
        if task_id in self.running:
            return
        self.running[task_id] = asyncio.create_task(
            self._run_with_lease(task_id, revision, command)
        )

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
                    "lease_renewed", self.node_id,
                    {"task_id": task_id, "revision": revision},
                )
            )

    async def _run(self, task_id: str, revision: int, command: dict) -> None:
        snapshot = command["snapshot"]
        sequence = 0
        try:
            env = build_route_env(command["route"], snapshot, self.route_store)
            run_command = RunCommand(
                prompt=command["prompt"],
                model=snapshot["model_id"],
                permission_mode=snapshot.get("permission_mode", "default"),
                tools=snapshot.get("tools", []),
                allowed_tools=snapshot.get("allowed_tools", []),
                disallowed_tools=snapshot.get("disallowed_tools", []),
                cwd=snapshot.get("working_directory"),
                env=env,
                start_sequence=int(command.get("event_sequence_start", 1)),
            )
            async for event in self.shell.run_session(run_command, task_id=task_id):
                sequence = int(event["sequence"])
                if task_id in self.cancelling and event["event_type"] == "result":
                    event = {
                        "sequence": sequence,
                        "event_type": "status",
                        "payload": {"state": "cancelling", "sdk_terminal": event["payload"]},
                    }
                self.spool.append(
                    task_id, sequence, event["event_type"], event["payload"]
                )
                await self._queue_pending(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            sequence += 1
            payload = {"code": "executor_error", "message": str(exc)}
            self.spool.append(task_id, sequence, "error", payload)
            await self._queue_pending(task_id)
        finally:
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
        shell: AgentShell | None = None,
    ) -> None:
        self.node_id = node_id
        self.spool = spool
        self.outbox: asyncio.Queue[Envelope] = asyncio.Queue()
        self.executor = NodeTaskExecutor(
            spool, route_store, self.outbox, node_id, shell=shell
        )
        self.dispatcher = TaskDispatcher(spool, self.executor)

    async def handle(self, message: Envelope) -> list[Envelope]:
        if message.type == "task_dispatch":
            try:
                decision = self.dispatcher.accept(message.payload)
            except Exception as exc:
                return [
                    envelope(
                        "task_rejected", self.node_id,
                        {"task_id": message.payload.get("task_id"), "reason": str(exc)},
                    )
                ]
            if decision in {DispatchDecision.ACCEPTED, DispatchDecision.DUPLICATE}:
                return [
                    envelope(
                        "task_accepted", self.node_id,
                        {"task_id": message.payload["task_id"],
                         "revision": message.payload["revision"],
                         "duplicate": decision == DispatchDecision.DUPLICATE},
                    )
                ]
            return [
                envelope(
                    "task_rejected", self.node_id,
                    {"task_id": message.payload["task_id"], "reason": decision.value},
                )
            ]
        if message.type == "task_events_ack":
            self.spool.acknowledge(
                str(message.payload["task_id"]), int(message.payload["through_sequence"])
            )
        elif message.type == "task_cancel":
            await self.executor.cancel(str(message.payload["task_id"]))
            return [
                envelope(
                    "task_cancelled", self.node_id,
                    {"task_id": message.payload["task_id"], "revision": message.payload["revision"]},
                )
            ]
        return []

    async def replay_pending(self) -> None:
        task_ids = sorted({event.task_id for event in self.spool.pending()})
        for task_id in task_ids:
            await self.executor._queue_pending(task_id)
