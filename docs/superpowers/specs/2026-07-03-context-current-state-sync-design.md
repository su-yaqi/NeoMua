# Context Current-State Sync Design

## Goal

Synchronize NeoMua's current-state documentation with the implementation on `master` while preserving the existing document structure, valid business explanations, and historical version snapshots.

## Sources of Truth

Use implementation artifacts in this order:

1. SQLModel entities and Alembic migrations for persisted data.
2. FastAPI router registration and route definitions for HTTP and WebSocket APIs.
3. TanStack file routes, sidebar configuration, and runtime components for UI state.
4. Docker Compose, environment configuration, and service health checks for deployment topology.
5. Current implementation code for runtime security, task, node, and artifact behavior.

When existing context text conflicts with these sources, update the context text. Do not infer capabilities that are not implemented.

## Scope

Update only current-state documentation:

- `context/readme.md`
- `context/project.md`
- `context/architecture.md`
- `context/data-schema.md`
- `context/apis.md`
- `context/ui.md`
- Relevant files under `context/modules/`, primarily `runtime_management`

Preserve `context/prds/` and `context/changelogs/` as historical snapshots. Do not create a new version or modify application code.

## Synchronization Rules

- Keep correct existing descriptions and make targeted edits rather than rewriting documents wholesale.
- Describe current behavior, not future plans.
- List public, node, and internal runtime interfaces separately where that distinction affects authentication or consumers.
- Document all persisted runtime entities, including security and reconciliation records that are absent from the current schema overview.
- Keep UI routes and permissions aligned with actual file routes and backend authorization.
- Record the current Compose service topology and local port assignments without exposing secret values.

## Expected Corrections

- Bring the context directory overview from its v0.2-era structure through v0.4.
- Expand the project purpose and implementation state to cover the control plane, runtime worker, model gateway, node daemon, and signed artifact delivery.
- Correct the architecture deployment diagram and document current service boundaries.
- Add missing runtime schema entities such as `node_handshake_nonce` and clarify key relationships and state machines.
- Replace compressed or incomplete runtime API entries with routes verified from source.
- Align the page tree, runtime pages, role visibility, and interactions with the current frontend.

## Verification

- Compare documented runtime tables against SQLModel table declarations and Alembic migrations.
- Compare documented endpoints against registered FastAPI routers and route decorators.
- Compare documented pages against TanStack file routes and sidebar entries.
- Compare deployment documentation against resolved Docker Compose configuration.
- Run Markdown and diff checks for malformed fences, trailing whitespace, accidental placeholders, and changes outside the approved scope.

The project test suite is outside this documentation-only task.
