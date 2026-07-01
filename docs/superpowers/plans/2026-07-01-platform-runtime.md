# Platform Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the namespace-scoped platform runtime, Model Gateway, persisted event stream, and multi-turn test UI.

**Architecture:** FastAPI remains the control plane and sole business-data writer. A separate `runtime-worker` manages Claude Agent SDK/CLI processes, while a separate `model-gateway` exposes Anthropic Messages API semantics and routes to existing namespace LLM configurations.

**Tech Stack:** Python 3.10, FastAPI, SQLModel, Alembic, PostgreSQL, claude-agent-sdk, httpx, WebSocket/SSE, React 19, TanStack Query/Router, Playwright, Docker Compose.

---

## File map

- `backend/app/runtime/models.py`: runtime, secret, session, task, and event SQLModel entities plus API schemas.
- `backend/app/runtime/policy.py`: role, model route, capability, and state-transition validation.
- `backend/app/runtime/repository.py`: transactional persistence, leases, and idempotent event append.
- `backend/app/runtime/security.py`: internal/Gateway token issue and verification plus event redaction.
- `backend/app/api/routes/runtimes.py`: browser-facing runtime/session/task/SSE endpoints.
- `backend/app/api/routes/runtime_internal.py`: authenticated worker event and dispatch endpoints.
- `runtime_worker/`: separate SDK/CLI process service.
- `model_gateway/`: separate Anthropic-compatible gateway service.
- `frontend/src/routes/_layout/system.runtimes.tsx`: runtime management route.
- `frontend/src/components/Runtimes/`: configuration and test UI.

### Task 1: Add role guards and runtime tables

**Files:**
- Create: `backend/app/runtime/__init__.py`
- Create: `backend/app/runtime/models.py`
- Create: `backend/app/runtime/policy.py`
- Create: `backend/app/alembic/versions/4f4e2a1b9c10_add_platform_runtime_tables.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/alembic/env.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/runtime/test_policy.py`
- Test: `backend/tests/runtime/test_models.py`

- [ ] **Step 1: Write failing role and state-policy tests**

```python
def test_developer_can_execute_but_cannot_manage():
    assert authorize_runtime_action(NamespaceRole.DEVELOPER, RuntimeAction.EXECUTE)
    assert not authorize_runtime_action(NamespaceRole.DEVELOPER, RuntimeAction.MANAGE)

def test_task_cannot_leave_terminal_state():
    with pytest.raises(InvalidTaskTransition):
        require_task_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
```

- [ ] **Step 2: Run tests and verify the missing-module failure**

Run: `cd backend && python3 -m pytest tests/runtime/test_policy.py -q`
Expected: FAIL with `ModuleNotFoundError: app.runtime`.

- [ ] **Step 3: Implement enums, guards, and focused SQLModel entities**

```python
class RuntimeType(str, Enum):
    PLATFORM = "platform"
    NODE = "node"

class RuntimeRouteMode(str, Enum):
    PLATFORM_GATEWAY = "platform_gateway"
    DIRECT_ANTHROPIC = "direct_anthropic"

class AgentEvent(SQLModel, table=True):
    __tablename__ = "agent_event"
    __table_args__ = (UniqueConstraint("task_id", "sequence"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(foreign_key="namespace.id", ondelete="CASCADE")
    task_id: uuid.UUID = Field(foreign_key="agent_task.id", ondelete="CASCADE")
    sequence: int
    event_type: AgentEventType
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
```

Add `require_namespace_runtime_user` (admin/developer) and `require_namespace_runtime_admin` dependencies without changing `require_namespace_admin` behavior. Import `app.runtime.models` in Alembic env so metadata is registered.

- [ ] **Step 4: Generate and inspect the migration**

Run: `cd backend && alembic revision --rev-id 4f4e2a1b9c10 --autogenerate -m "add platform runtime tables"`
Expected: tables `runtime_profile`, `runtime_secret`, `agent_session`, `agent_task`, and `agent_event`; all namespace foreign keys use cascade; no unrelated drops.

- [ ] **Step 5: Run focused tests and migration round trip**

Run: `cd backend && python3 -m pytest tests/runtime/test_policy.py tests/runtime/test_models.py -q && alembic upgrade head`
Expected: PASS and one Alembic head.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime backend/app/api/deps.py backend/app/alembic backend/tests
git commit -m "feat: add runtime domain model and policies"
```

### Task 2: Implement platform runtime configuration API

**Files:**
- Create: `backend/app/runtime/repository.py`
- Create: `backend/app/runtime/service.py`
- Create: `backend/app/api/routes/runtimes.py`
- Modify: `backend/app/api/main.py`
- Test: `backend/tests/api/routes/test_runtimes.py`

- [ ] **Step 1: Write failing namespace, role, and model-reference API tests**

```python
def test_admin_can_create_platform_runtime(client, namespace_admin_headers, llm_config):
    response = client.put("/api/v1/runtimes/platform", headers=namespace_admin_headers,
        json={"route_mode": "platform_gateway", "provider_config_id": str(llm_config.id),
              "model_id": "claude-sonnet", "permission_mode": "default"})
    assert response.status_code == 200

def test_cross_namespace_model_is_rejected(client, namespace_admin_headers, other_llm_config):
    response = client.put("/api/v1/runtimes/platform", headers=namespace_admin_headers,
        json={"route_mode": "platform_gateway", "provider_config_id": str(other_llm_config.id),
              "model_id": "model-x", "permission_mode": "default"})
    assert response.status_code == 404
```

- [ ] **Step 2: Verify failure**

Run: `cd backend && python3 -m pytest tests/api/routes/test_runtimes.py -q`
Expected: FAIL with 404 because the route is absent.

- [ ] **Step 3: Implement explicit route-mode validation and encrypted platform secrets**

```python
def validate_runtime_route(session: Session, namespace_id: UUID, data: RuntimeProfileUpsert) -> None:
    if data.route_mode is RuntimeRouteMode.PLATFORM_GATEWAY:
        require_enabled_namespace_model(session, namespace_id, data.provider_config_id, data.model_id)
    elif not data.base_url or not data.secret_inputs:
        raise HTTPException(400, "direct_anthropic requires base_url and credentials")
    if data.permission_mode == "bypassPermissions":
        raise HTTPException(400, "bypassPermissions is not allowed")
```

Expose `GET/PUT /api/v1/runtimes/platform` and `POST /api/v1/runtimes/platform/validate`. Reuse `seal_secret_payload`; never serialize `secret_ciphertext`.

- [ ] **Step 4: Run route tests**

Run: `cd backend && python3 -m pytest tests/api/routes/test_runtimes.py -q`
Expected: PASS for admin, 403 for developer updates, 403 for user reads, and 404 for cross-namespace references.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime backend/app/api/routes backend/app/api/main.py backend/tests/api/routes/test_runtimes.py
git commit -m "feat: add platform runtime configuration API"
```

### Task 3: Build the authenticated worker contract and SDK adapter

**Files:**
- Create: `backend/app/runtime/security.py`
- Create: `backend/app/api/routes/runtime_internal.py`
- Create: `runtime_worker/pyproject.toml`
- Create: `runtime_worker/runtime_worker/agent_shell.py`
- Create: `runtime_worker/runtime_worker/client.py`
- Create: `runtime_worker/runtime_worker/main.py`
- Create: `runtime_worker/Dockerfile`
- Modify: `pyproject.toml`
- Test: `backend/tests/api/routes/test_runtime_internal.py`
- Test: `runtime_worker/tests/test_agent_shell.py`

- [ ] **Step 1: Write failing internal-token and message-normalization tests**

```python
def test_internal_endpoint_rejects_user_jwt(client, superuser_token_headers):
    response = client.post("/api/v1/internal/runtime/events", headers=superuser_token_headers, json={})
    assert response.status_code == 403

def test_normalize_result_message():
    event = normalize_sdk_message(FakeResult(session_id="s1", result="done"), sequence=4)
    assert (event.event_type, event.sequence) == ("result", 4)
```

- [ ] **Step 2: Add the official SDK dependency and lock normally**

Add `claude-agent-sdk==0.2.110` to `runtime_worker/pyproject.toml` and add `runtime_worker` to the root uv workspace. Run: `UV_CACHE_DIR=/private/tmp/uv-cache python3 -m uv lock`.
Expected: lock resolves the SDK and its supported `mcp`/`anyio` dependencies; if resolution fails, stop and report instead of vendoring SDK code.

- [ ] **Step 3: Implement AgentShell without private SDK imports**

```python
class AgentShell:
    async def run_session(self, command: RunCommand) -> AsyncIterator[RuntimeEvent]:
        options = ClaudeAgentOptions(model=command.model, tools=command.tools,
            allowed_tools=command.allowed_tools, disallowed_tools=command.disallowed_tools,
            permission_mode=command.permission_mode, cwd=command.cwd, env=command.env)
        async with ClaudeSDKClient(options=options) as client:
            await client.query(command.prompt, session_id=command.sdk_session_id or "default")
            async for message in client.receive_response():
                yield normalize_sdk_message(message)
```

Use only public `claude_agent_sdk` APIs. Implement interrupt and deterministic subprocess cleanup.

- [ ] **Step 4: Run worker and internal API tests**

Run: `cd runtime_worker && python3 -m pytest tests/test_agent_shell.py -q`
Run: `cd backend && python3 -m pytest tests/api/routes/test_runtime_internal.py -q`
Expected: PASS; user JWT is rejected and a scoped internal token cannot cross task/namespace boundaries.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock runtime_worker backend/app/runtime/security.py backend/app/api/routes/runtime_internal.py backend/tests/api/routes/test_runtime_internal.py
git commit -m "feat: add isolated Claude runtime worker"
```

### Task 4: Build Model Gateway with fail-closed capabilities

**Files:**
- Create: `model_gateway/pyproject.toml`
- Create: `model_gateway/model_gateway/app.py`
- Create: `model_gateway/model_gateway/auth.py`
- Create: `model_gateway/model_gateway/capabilities.py`
- Create: `model_gateway/model_gateway/providers/anthropic.py`
- Create: `model_gateway/model_gateway/providers/openai_compatible.py`
- Create: `model_gateway/Dockerfile`
- Test: `model_gateway/tests/test_anthropic_passthrough.py`
- Test: `model_gateway/tests/test_openai_translation.py`
- Test: `model_gateway/tests/test_capability_rejection.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing pass-through, tool-call, stream, and rejection contract tests**

```python
def test_rejects_unsupported_thinking(client, gateway_headers):
    response = client.post("/v1/messages", headers=gateway_headers,
        json={"model": "openai-model", "thinking": {"type": "enabled", "budget_tokens": 1024},
              "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_model_capability"
```

- [ ] **Step 2: Implement provider adapters behind one interface**

```python
class ProviderAdapter(Protocol):
    def validate(self, request: AnthropicMessageRequest) -> None: ...
    async def stream(self, request: AnthropicMessageRequest) -> AsyncIterator[bytes]: ...
```

Map `tool_use/tool_result`, stop reasons, and SSE event ordering explicitly. Do not map thinking or server tools for providers lacking them.

- [ ] **Step 3: Verify contracts**

Run: `cd model_gateway && python3 -m pytest tests -q`
Expected: PASS for Anthropic pass-through, supported OpenAI-compatible tool round trips and explicit unsupported-capability errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock model_gateway
git commit -m "feat: add fail-closed model gateway"
```

### Task 5: Add task persistence, SSE, and multi-turn runtime UI

**Files:**
- Modify: `backend/app/runtime/repository.py`
- Modify: `backend/app/api/routes/runtimes.py`
- Test: `backend/tests/runtime/test_event_repository.py`
- Modify: `frontend/src/client/tenantApi.ts`
- Create: `frontend/src/routes/_layout/system.runtimes.tsx`
- Create: `frontend/src/components/Runtimes/PlatformRuntimeCard.tsx`
- Create: `frontend/src/components/Runtimes/RuntimeConfigDialog.tsx`
- Create: `frontend/src/components/Runtimes/TestConversationSheet.tsx`
- Modify: `frontend/src/components/Sidebar/AppSidebar.tsx`
- Test: `frontend/tests/runtimes.spec.ts`

- [ ] **Step 1: Write failing idempotent event and UI role tests**

```python
def test_append_event_is_idempotent(session, task):
    append_event(session, task.id, 1, "assistant_message", {"text": "a"})
    append_event(session, task.id, 1, "assistant_message", {"text": "a"})
    assert count_events(session, task.id) == 1
```

```ts
test("developer can test but cannot configure runtime", async ({ page }) => {
  await page.goto("/system/runtimes")
  await expect(page.getByRole("button", { name: "功能测试" })).toBeVisible()
  await expect(page.getByRole("button", { name: "配置" })).toHaveCount(0)
})
```

- [ ] **Step 2: Implement session/task endpoints and SSE replay**

Add `POST /sessions`, `POST /sessions/{id}/messages`, `POST /tasks/{id}/cancel`, `GET /tasks/{id}/events`, and `GET /tasks/{id}/stream?after_sequence=N`. SSE must emit persisted events first, then live events, and resume from `Last-Event-ID`.

- [ ] **Step 3: Implement the page and conversation sheet**

Use TanStack Query for resource state and native `fetch()` streaming for authenticated SSE because browser `EventSource` cannot set the bearer header. Render event types separately and keep tool payloads collapsed by default.

- [ ] **Step 4: Regenerate client, build, and run tests**

Run: `./scripts/generate-client.sh`
Run: `cd frontend && npm run build`
Run: `cd backend && python3 -m pytest tests/runtime tests/api/routes/test_runtimes.py -q`
Run: `cd frontend && PLAYWRIGHT_NO_WEBSERVER=1 npx playwright test tests/runtimes.spec.ts`
Expected: all PASS; route tree and generated client compile.

- [ ] **Step 5: Commit**

```bash
git add backend frontend/src frontend/tests openapi.json
git commit -m "feat: add platform runtime test experience"
```

### Task 6: Compose integration and phase-one acceptance

**Files:**
- Modify: `compose.yml`
- Modify: `compose.override.yml`
- Create: `.env.example`
- Test: `backend/tests/integration/test_platform_runtime_e2e.py`
- Modify: `context/prds/v0.4/namespace-platform-runtime.md`

- [ ] **Step 1: Add runtime-worker and model-gateway services with health checks**

Configure internal-only service ports, explicit secrets, restart policy, backend dependency, and no host port in production Compose. Do not mount the Docker socket.

- [ ] **Step 2: Run Compose validation and end-to-end smoke test**

Run: `docker compose config`
Run: `docker compose up -d --build backend runtime-worker model-gateway frontend`
Run: `cd backend && python3 -m pytest tests/integration/test_platform_runtime_e2e.py -q`
Expected: health checks pass; a multi-turn test produces ordered persisted events and the second turn uses the same session.

- [ ] **Step 3: Run regression suites**

Run: `cd backend && python3 -m pytest tests -q`
Run: `cd frontend && npm run build`
Expected: all existing tests pass.

- [ ] **Step 4: Record actual behavior and commit**

Update only implemented current-state docs after acceptance; add v0.4 changelog only when all four v0.4 phases are complete.

```bash
git add compose.yml compose.override.yml .env.example backend/tests/integration context
git commit -m "feat: complete platform runtime phase"
```
