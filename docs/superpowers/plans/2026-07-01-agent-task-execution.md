# Agent Task Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably dispatch ordinary Agent tasks to platform or node runtimes and persist the complete ordered execution process.

**Architecture:** FastAPI persists immutable task snapshots and controls state transitions. Executors acknowledge versioned dispatches, hold leases, spool unacknowledged events, and never auto-rerun interrupted work.

**Tech Stack:** Existing runtime control plane, versioned WebSocket envelopes, Claude Agent SDK, SQLModel/PostgreSQL, durable local SQLite spool, SSE, React/Playwright.

---

### Task 1: Complete task state machine, snapshots, and leases

**Files:**
- Modify: `backend/app/runtime/models.py`
- Modify: `backend/app/runtime/policy.py`
- Modify: `backend/app/runtime/repository.py`
- Create: `backend/app/alembic/versions/6b604c3d1e32_add_agent_task_execution_fields.py`
- Test: `backend/tests/runtime/test_task_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_retry_creates_new_task_linked_to_original(service, failed_task):
    retry = service.retry(failed_task.id)
    assert retry.id != failed_task.id
    assert retry.retry_of_task_id == failed_task.id

def test_expired_running_lease_becomes_interrupted(service, running_task, clock):
    clock.advance(minutes=6)
    service.expire_leases()
    assert running_task.status == TaskStatus.INTERRUPTED
```

- [ ] **Step 2: Implement immutable snapshots and explicit transitions**

Snapshot fields include route mode, provider/model, endpoint metadata, tools, allowed/disallowed tools, permission mode, working directory roots, timeout, runtime revision and executor versions. Define legal transitions as a constant map and lock rows before transition.

- [ ] **Step 3: Migrate and test**

Run: `cd backend && alembic revision --rev-id 6b604c3d1e32 --autogenerate -m "add task execution lifecycle" && alembic upgrade head && python3 -m pytest tests/runtime/test_task_lifecycle.py -q`
Expected: PASS and illegal terminal-state transitions raise conflict.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime backend/app/alembic backend/tests/runtime/test_task_lifecycle.py
git commit -m "feat: add reliable task lifecycle"
```

### Task 2: Add task APIs and fail-closed runtime validation

**Files:**
- Modify: `backend/app/api/routes/runtimes.py`
- Modify: `backend/app/runtime/service.py`
- Test: `backend/tests/api/routes/test_agent_tasks.py`

- [ ] **Step 1: Write failing permission and capability tests**

```python
def test_developer_cannot_dispatch_admin_action(client, developer_headers, node):
    response = client.post("/api/v1/runtime-tasks", headers=developer_headers,
        json={"runtime_id": str(node.runtime_id), "prompt": "rotate credentials", "task_kind": "admin"})
    assert response.status_code == 403
```

- [ ] **Step 2: Implement create/read/cancel/retry APIs**

Add `POST/GET /api/v1/runtime-tasks`, `GET /{id}`, `POST /{id}/cancel`, and `POST /{id}/retry`. Resolve route and capabilities before persistence; reject offline target, cross-namespace target, unsupported model feature, unsafe permission mode, and non-allowlisted working root.

- [ ] **Step 3: Run API tests**

Run: `cd backend && python3 -m pytest tests/api/routes/test_agent_tasks.py -q`
Expected: PASS for admin/developer ordinary tasks, 403 for user/admin actions, and 422 for incompatible capabilities.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes/runtimes.py backend/app/runtime/service.py backend/tests/api/routes/test_agent_tasks.py
git commit -m "feat: expose Agent task APIs"
```

### Task 3: Implement dispatch acknowledgement and duplicate suppression

**Files:**
- Modify: `backend/app/runtime/connections.py`
- Modify: `backend/app/api/routes/node_socket.py`
- Create: `node_runtime/node_runtime/tasks.py`
- Test: `backend/tests/runtime/test_task_dispatch.py`
- Test: `node_runtime/tests/test_task_dispatch.py`

- [ ] **Step 1: Write failing duplicate-dispatch test**

```python
async def test_duplicate_revision_starts_one_executor(dispatcher, daemon):
    command = Dispatch(task_id=uuid4(), revision=1, snapshot=valid_snapshot())
    await daemon.handle(command)
    await daemon.handle(command)
    assert daemon.executor.start_count(command.task_id) == 1
```

- [ ] **Step 2: Implement `dispatch/accepted/rejected/lease_renewed/cancel` messages**

Persist accepted task ID/revision locally before starting SDK. On duplicate revision return the stored acknowledgement. Reject stale revisions and unknown target roots. Renew leases only while the matching local execution is alive.

- [ ] **Step 3: Run protocol tests**

Run: `cd backend && python3 -m pytest tests/runtime/test_task_dispatch.py -q`
Run: `cd node_runtime && python3 -m pytest tests/test_task_dispatch.py -q`
Expected: PASS; duplicate dispatch starts exactly once.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime/connections.py backend/app/api/routes/node_socket.py node_runtime
git commit -m "feat: add idempotent task dispatch protocol"
```

### Task 4: Add durable event spool and ordered replay

**Files:**
- Create: `node_runtime/node_runtime/spool.py`
- Modify: `node_runtime/node_runtime/tasks.py`
- Modify: `node_runtime/node_runtime/reconcile.py`
- Modify: `backend/app/runtime/repository.py`
- Test: `node_runtime/tests/test_spool.py`
- Test: `backend/tests/runtime/test_event_replay.py`

- [ ] **Step 1: Write failing crash/replay tests**

```python
def test_unacked_result_survives_process_restart(tmp_path):
    first = EventSpool(tmp_path / "spool.db")
    first.append(task_id="t1", sequence=8, kind="result", payload={"text": "done"})
    second = EventSpool(tmp_path / "spool.db")
    assert second.pending("t1")[0].sequence == 8
```

- [ ] **Step 2: Implement SQLite spool and acknowledgements**

Use SQLite transactions and unique `(task_id, sequence)`. Append before network send; delete only through contiguous acknowledged sequence. Cap disk usage and reject new tasks with `spool_capacity_exceeded` instead of dropping events.

- [ ] **Step 3: Implement server idempotent append and reconnect replay**

Compare duplicate payload hashes: identical duplicates receive ack; conflicting duplicates fail the task with `event_sequence_conflict`. Reconcile from the platform's last contiguous sequence.

- [ ] **Step 4: Run replay tests**

Run: `cd node_runtime && python3 -m pytest tests/test_spool.py -q`
Run: `cd backend && python3 -m pytest tests/runtime/test_event_replay.py -q`
Expected: PASS through simulated daemon restart and network loss.

- [ ] **Step 5: Commit**

```bash
git add node_runtime backend/app/runtime/repository.py backend/tests/runtime/test_event_replay.py
git commit -m "feat: add durable task event replay"
```

### Task 5: Wire Node AgentShell and model-route secrets

**Files:**
- Create: `node_runtime/node_runtime/agent_shell.py`
- Create: `node_runtime/node_runtime/model_route.py`
- Modify: `node_runtime/node_runtime/tasks.py`
- Modify: `model_gateway/model_gateway/auth.py`
- Test: `node_runtime/tests/test_model_route.py`
- Test: `model_gateway/tests/test_node_token_scope.py`

- [ ] **Step 1: Write failing token-scope and direct-compatibility tests**

```python
def test_gateway_token_cannot_change_model(client, token_for_model_a):
    response = client.post("/v1/messages", headers=bearer(token_for_model_a),
        json={"model": "model-b", "messages": [{"role": "user", "content": "x"}]})
    assert response.status_code == 403
```

- [ ] **Step 2: Implement route construction**

For `platform_gateway`, request a short-lived token bound to namespace/node/task/model and set CLI `ANTHROPIC_BASE_URL` to Gateway. For `direct_anthropic`, read the secret only from node protected storage and require a recorded compatibility success for the exact endpoint/model fingerprint.

- [ ] **Step 3: Execute through public SDK APIs and normalize every message**

Reuse the same message normalization contract as runtime-worker. Never set `bypassPermissions`; pass only snapshot-approved tools and directories. Cancellation calls SDK interrupt and waits for a terminal event before forced process cleanup.

- [ ] **Step 4: Run route and SDK smoke tests**

Run: `cd node_runtime && python3 -m pytest tests/test_model_route.py -q`
Run: `cd model_gateway && python3 -m pytest tests/test_node_token_scope.py -q`
Expected: PASS; token scope violations and unverified direct endpoints are rejected.

- [ ] **Step 5: Commit**

```bash
git add node_runtime model_gateway
git commit -m "feat: execute node tasks with scoped model routes"
```

### Task 6: Add task UI and full acceptance tests

**Files:**
- Modify: `frontend/src/client/tenantApi.ts`
- Create: `frontend/src/components/Runtimes/DispatchTaskSheet.tsx`
- Create: `frontend/src/routes/_layout/system.runtimes.tasks.$taskId.tsx`
- Create: `frontend/src/components/Runtimes/TaskEventTimeline.tsx`
- Test: `frontend/tests/runtime-tasks.spec.ts`
- Test: `backend/tests/integration/test_node_task_recovery.py`

- [ ] **Step 1: Write failing UI test for interrupted task behavior**

```ts
test("interrupted task offers explicit retry only", async ({ page }) => {
  await page.goto("/system/runtimes/tasks/test-task")
  await expect(page.getByText("已中断")).toBeVisible()
  await expect(page.getByRole("button", { name: "重新执行" })).toBeVisible()
  await expect(page.getByText("自动重试中")).toHaveCount(0)
})
```

- [ ] **Step 2: Implement dispatch sheet and event timeline**

Render messages, tool calls/results, statuses and errors distinctly; collapse tool payloads by default; show immutable runtime snapshot and retry lineage. Cancel and retry buttons use idempotency keys.

- [ ] **Step 3: Run recovery and regression suites**

Run: `cd backend && python3 -m pytest tests/integration/test_node_task_recovery.py -q`
Run: `cd frontend && npm run build && PLAYWRIGHT_NO_WEBSERVER=1 npx playwright test tests/runtime-tasks.spec.ts`
Expected: completed-unacked result replays; running task becomes interrupted; no automatic rerun.

- [ ] **Step 4: Commit**

```bash
git add frontend backend/tests/integration/test_node_task_recovery.py
git commit -m "feat: complete reliable Agent task execution"
```
