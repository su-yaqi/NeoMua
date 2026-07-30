# NeoMua Agent Runtime Design

## 1. Scope and decisions

NeoMua v0.4 adds namespace-scoped platform and node runtimes based on the official `claude-agent-sdk` Python package and its bundled Claude Code CLI. The implementation will not fork the SDK, modify the CLI, implement a private Transport, or create a second Agent Loop.

The approved topology is:

```text
Browser
  -> FastAPI control plane -> PostgreSQL
       | internal authenticated channel
       +-> runtime-worker -> Claude Agent SDK / CLI
       +-> Model Gateway -> configured model provider
       +<- WSS <- Node Daemon -> local SDK / CLI
```

FastAPI remains the only business-data write boundary. `runtime-worker` owns platform CLI subprocesses. Model Gateway exposes Anthropic Messages API semantics and either passes through a compatible upstream or explicitly converts a supported provider protocol. Node Daemon uses an outbound authenticated WebSocket, so nodes require no inbound public port.

## 2. Component boundaries

### FastAPI control plane
- Enforces namespace and role permissions.
- Owns runtime profiles, node identity, enrollment, sessions, tasks, events, artifacts and audit records.
- Persists a task before dispatch and validates every state transition.
- Streams persisted events to the browser over SSE.
- Issues narrowly scoped, short-lived internal and Gateway tokens.

### runtime-worker
- Maintains an internal authenticated connection to FastAPI.
- Translates task snapshots into `ClaudeAgentOptions`.
- Uses `ClaudeSDKClient` for interactive sessions and `query()` where a one-shot lifecycle is appropriate.
- Normalizes SDK messages into the shared `agent_event` schema.
- Owns graceful interruption, timeout and CLI subprocess cleanup.
- Does not write business tables directly.

### Model Gateway
- Accepts Anthropic Messages API requests from platform or node CLI processes.
- Authorizes namespace, runtime, task and model scope before loading provider credentials.
- Passes through Anthropic-compatible requests or converts only capability-mapped provider protocols.
- Rejects unsupported thinking, server tools, tool semantics or streaming behavior before execution; it never silently drops them.
- Keeps provider credentials inside the platform boundary.

### Node Daemon
- Installs as a managed OS service and stores its device identity in protected local storage.
- Maintains WSS, heartbeat, reconnection, local event spool and content staging.
- Runs the same AgentShell abstraction used by `runtime-worker`.
- Uses either a task-scoped Gateway token or a locally stored credential for a verified Anthropic-compatible API.
- Enforces server policy locally as a second control, not as a replacement for server validation.

## 3. Identity and connectivity

Enrollment tokens are random, single-use, valid for ten minutes and stored only as hashes. Successful enrollment binds a node public key to one namespace and returns a device credential. Device credentials last 90 days and rotate while online from 30 days before expiry. An expired or revoked credential requires explicit re-enrollment.

Heartbeat runs every 20 seconds; three missed intervals mark the node offline without removing its pairing. After a day-long shutdown, the daemon reconnects with the same identity, reports its last acknowledged event, local pending state and installed content versions, then reconciles differences.

Every WebSocket envelope contains `type`, `protocol_version`, `message_id`, `correlation_id`, `node_id`, `sent_at` and `payload`. Unsupported protocol versions and message types receive explicit structured errors.

## 4. Execution and persistence

A session represents multi-turn context. A task represents one execution attempt. Events are append-only records for user messages, assistant messages, tool calls, tool results, status, errors and final results. `task_id + sequence` is unique, enabling idempotent retransmission.

Task creation captures an immutable snapshot of model route, model ID, tools, permission mode, working directories, timeout and relevant runtime version. Dispatch uses task ID and revision. The executor first returns `accepted` or `rejected`; accepted tasks hold an expiring lease. Duplicate dispatch does not create a second Agent Loop.

Until a final event is acknowledged, Node Daemon keeps events in a durable local spool. A completed-but-unacknowledged result is uploaded after reconnection. A task running when the node shuts down becomes `interrupted` and is not automatically rerun. Explicit retry creates a new task linked to the original.

## 5. Model routing

Each runtime uses exactly one of:

- `platform_gateway`: references a namespace `llm_provider_config` and enabled model. A node receives a short-lived token scoped to namespace, node, task and model; it never receives the provider secret.
- `direct_anthropic`: stores endpoint and secret within the execution host's protected store. For the platform runtime this is encrypted in `runtime_secret`; for a node it remains only in node-local protected storage and the platform retains metadata and a mask. Activation requires an Anthropic Messages API compatibility test. Non-compatible endpoints cannot use this mode.

Gateway conversion is centralized. A provider capability matrix covers streaming, tool calls, stop reasons and supported request features. A task requiring an unsupported feature is rejected before model invocation.

## 6. Content delivery

Content is an immutable signed artifact with a manifest, logical target, file list, hashes, sizes and version. Allowed target classes are `agents`, `skills`, `mcp`, `cli_config` and explicitly enabled `workspace_content`. Absolute paths, traversal, symlink escapes and unregistered targets are rejected both by FastAPI and Node Daemon.

The daemon downloads through a short-lived URL, validates signature and hashes, writes a same-filesystem staging directory, then atomically switches versions. Failure removes staging and preserves the old version. v0.4 content delivery cannot execute scripts or arbitrary commands.

## 7. Roles and UI

- Namespace Admin: configure runtimes, models and credentials; enroll/revoke nodes; test conversations; dispatch tasks; publish and roll back content.
- Namespace Developer: test conversations and dispatch ordinary tasks.
- Namespace User: runtime navigation and APIs are unavailable.

`/system/runtimes` has a fixed platform-runtime card followed by a node table. Platform configuration and multi-turn testing use dialogs/drawers. Node details cover status, model route, versions, task history, content versions and credentials. Sensitive values are displayed once at creation or rotation.

## 8. Error handling and observability

Failures use stable structured codes across API, WebSocket and event records. No path changes model, protocol, permissions or tool policy as a fallback. Node status, task state and deployment state are separate state machines. Illegal transitions return conflict errors.

Logs include correlation ID, namespace ID, runtime/node ID and task/release ID, but exclude secrets and raw enrollment tokens. Event payloads pass through centralized redaction before persistence. Operational metrics cover active connections, heartbeat age, task latency and terminal states, Gateway latency/errors, CLI process health, spool depth and deployment outcomes.

## 9. Verification strategy

- Unit tests: state machines, role guards, encryption/hashing, token consumption, leases, event ordering, manifest validation and path safety.
- Contract tests: WebSocket envelopes, reconnect/replay, Gateway pass-through and conversion, streaming, tool-call round trips and capability rejection.
- Integration tests: real SDK/CLI smoke conversation, session continuation, cancellation, timeout and subprocess cleanup.
- Node tests: enrollment, rotation, revocation, one-day offline recovery, durable replay, duplicate commands and atomic rollback.
- UI tests: role visibility, configuration, multi-turn testing, node status, enrollment and deployment flows.
- Compose E2E: platform task, node task, Gateway routing, disconnection recovery and artifact deployment.

## 10. Delivery phases

1. Platform runtime, runtime-worker, Gateway, persistence and UI testing flow.
2. Node package, enrollment, WSS, heartbeat, rotation and offline recovery.
3. Reliable task protocol, full event persistence, cancellation and explicit retry.
4. Signed artifact storage, deployment, atomic application and rollback.

Each phase has a separate implementation plan checkpoint and must pass its acceptance suite before the next phase begins.
