# Node Enrollment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a command-line installed Node Daemon with secure one-time enrollment, outbound WSS heartbeat, credential rotation, revocation, and one-day offline recovery.

**Architecture:** FastAPI owns node identity and credentials. The daemon persists its private identity locally, initiates all network connections, and reconciles its state after reconnecting; heartbeat loss changes presence only and never removes pairing.

**Tech Stack:** FastAPI WebSocket, SQLModel/PostgreSQL, Ed25519 or platform-approved asymmetric device keys, JWT/JWS service tokens, Python daemon, Typer, pytest, Docker Compose.

---

### Task 1: Add node identity, enrollment, and credential persistence

**Files:**
- Modify: `backend/app/runtime/models.py`
- Modify: `backend/app/runtime/repository.py`
- Create: `backend/app/runtime/enrollment.py`
- Create: `backend/app/alembic/versions/5a5f3b2c0d21_add_runtime_nodes.py`
- Test: `backend/tests/runtime/test_enrollment.py`

- [ ] **Step 1: Write failing single-use and expiry tests**

```python
def test_enrollment_token_is_single_use(session, namespace_id, admin_id):
    raw, record = create_enrollment_token(session, namespace_id, admin_id, ttl=timedelta(minutes=10))
    consume_enrollment_token(session, raw)
    with pytest.raises(EnrollmentTokenInvalid):
        consume_enrollment_token(session, raw)
    assert record.token_hash != raw
```

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && python3 -m pytest tests/runtime/test_enrollment.py -q`
Expected: FAIL because enrollment service does not exist.

- [ ] **Step 3: Implement `runtime_node`, `node_enrollment_token`, and `node_credential`**

Hash enrollment tokens with HMAC-SHA256 using a dedicated derived key; consume with a row lock and `consumed_at IS NULL AND expires_at > now()`. Store device public key/fingerprint, validity, rotation lineage, and revocation time; never store node private keys.

- [ ] **Step 4: Generate migration and run tests**

Run: `cd backend && alembic revision --rev-id 5a5f3b2c0d21 --autogenerate -m "add runtime nodes" && alembic upgrade head && python3 -m pytest tests/runtime/test_enrollment.py -q`
Expected: PASS and no plaintext token column.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime backend/app/alembic backend/tests/runtime/test_enrollment.py
git commit -m "feat: add secure node enrollment domain"
```

### Task 2: Add admin enrollment and credential APIs

**Files:**
- Modify: `backend/app/api/routes/runtimes.py`
- Create: `backend/app/api/routes/node_enrollment.py`
- Modify: `backend/app/api/main.py`
- Test: `backend/tests/api/routes/test_node_enrollment.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_token_is_returned_once(client, namespace_admin_headers):
    created = client.post("/api/v1/runtimes/nodes/enrollment-tokens", headers=namespace_admin_headers)
    assert created.status_code == 201 and created.json()["token"]
    listed = client.get("/api/v1/runtimes/nodes/enrollment-tokens", headers=namespace_admin_headers)
    assert "token" not in listed.json()["data"][0]
```

- [ ] **Step 2: Implement admin and public enrollment endpoints**

Add admin-only token create/revoke/list and node-facing `POST /api/v1/node/enroll`. Enrollment accepts token, node public key, hostname, OS/architecture and versions; it atomically consumes the token and returns node ID plus signed device credential.

- [ ] **Step 3: Run API tests**

Run: `cd backend && python3 -m pytest tests/api/routes/test_node_enrollment.py -q`
Expected: PASS for one-time display, 403 for developer creation, 409 for reused token, and 410 for expired token.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api backend/tests/api/routes/test_node_enrollment.py
git commit -m "feat: expose node enrollment APIs"
```

### Task 3: Scaffold installable Node Daemon and protected state store

**Files:**
- Create: `node_runtime/pyproject.toml`
- Create: `node_runtime/node_runtime/cli.py`
- Create: `node_runtime/node_runtime/config.py`
- Create: `node_runtime/node_runtime/identity.py`
- Create: `node_runtime/node_runtime/service.py`
- Create: `node_runtime/packaging/neomua-node.service`
- Create: `node_runtime/tests/test_identity.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing identity persistence tests**

```python
def test_identity_file_is_owner_only(tmp_path):
    store = IdentityStore(tmp_path / "identity.json")
    store.save(DeviceIdentity(node_id="n1", private_key="secret", credential="token"))
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
```

- [ ] **Step 2: Implement `neomua-node install` and `run`**

`install --platform-url --enrollment-token` generates a device keypair, verifies HTTPS, enrolls, stores state with mode `0600`, writes non-secret config separately, and installs the OS service only after successful enrollment. If the OS service manager is unsupported, stop with an explicit error instead of starting an unmanaged background process.

- [ ] **Step 3: Lock and test**

Run: `UV_CACHE_DIR=/private/tmp/uv-cache python3 -m uv lock`
Run: `cd node_runtime && python3 -m pytest tests/test_identity.py -q`
Expected: PASS; no private key appears in logs or CLI output.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock node_runtime
git commit -m "feat: add installable node daemon"
```

### Task 4: Implement WSS handshake, heartbeat, and connection registry

**Files:**
- Create: `backend/app/runtime/connections.py`
- Create: `backend/app/api/routes/node_socket.py`
- Modify: `backend/app/api/main.py`
- Create: `node_runtime/node_runtime/protocol.py`
- Create: `node_runtime/node_runtime/connection.py`
- Test: `backend/tests/runtime/test_node_socket.py`
- Test: `node_runtime/tests/test_connection.py`

- [ ] **Step 1: Write failing protocol and offline-threshold tests**

```python
def test_three_missed_heartbeats_marks_presence_offline(clock, registry, node):
    registry.connected(node.id, at=clock.now())
    clock.advance(seconds=61)
    assert registry.presence(node.id) == NodePresence.OFFLINE
    assert node.revoked_at is None
```

- [ ] **Step 2: Implement versioned envelopes and authenticated WSS**

```python
class Envelope(BaseModel):
    type: str
    protocol_version: Literal["1"]
    message_id: UUID
    correlation_id: UUID | None
    node_id: UUID
    sent_at: datetime
    payload: dict[str, Any]
```

Authenticate before `websocket.accept()`. Reject unknown versions/types with a structured error. Send heartbeat every 20 seconds, infer offline after 60 seconds, and use capped exponential backoff with jitter on the daemon.

- [ ] **Step 3: Run socket tests**

Run: `cd backend && python3 -m pytest tests/runtime/test_node_socket.py -q`
Run: `cd node_runtime && python3 -m pytest tests/test_connection.py -q`
Expected: PASS for auth, heartbeat, duplicate hello, reconnect and version rejection.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime/connections.py backend/app/api/routes/node_socket.py backend/tests node_runtime
git commit -m "feat: add authenticated node websocket"
```

### Task 5: Implement 90-day rotation and offline reconciliation

**Files:**
- Modify: `backend/app/runtime/enrollment.py`
- Modify: `backend/app/api/routes/node_socket.py`
- Create: `node_runtime/node_runtime/reconcile.py`
- Test: `backend/tests/runtime/test_credential_rotation.py`
- Test: `node_runtime/tests/test_reconcile.py`

- [ ] **Step 1: Write failing time-travel tests**

```python
def test_node_reconnects_after_one_day_with_same_identity(harness):
    node_id = harness.enroll()
    harness.disconnect()
    harness.clock.advance(days=1)
    hello = harness.reconnect()
    assert hello.node_id == node_id
```

- [ ] **Step 2: Implement rotation and reconciliation**

At 30 days before credential expiry, request a new credential signed for the existing device key, atomically save it, reconnect, then acknowledge rotation so the old credential can be retired. Reconciliation reports last acknowledged event, interrupted tasks, spool range, runtime configuration revision and installed artifact versions.

- [ ] **Step 3: Test expiry and revocation without fallback**

Run: `cd backend && python3 -m pytest tests/runtime/test_credential_rotation.py -q`
Run: `cd node_runtime && python3 -m pytest tests/test_reconcile.py -q`
Expected: one-day recovery PASS; expired/revoked credential rejected and requires explicit re-enrollment.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime backend/app/api/routes/node_socket.py node_runtime
git commit -m "feat: add node credential rotation and recovery"
```

### Task 6: Add node management UI and acceptance tests

**Files:**
- Modify: `frontend/src/client/tenantApi.ts`
- Modify: `frontend/src/routes/_layout/system.runtimes.tsx`
- Create: `frontend/src/components/Runtimes/NodeTable.tsx`
- Create: `frontend/src/components/Runtimes/EnrollNodeDialog.tsx`
- Create: `frontend/src/routes/_layout/system.runtimes.nodes.$nodeId.tsx`
- Test: `frontend/tests/runtime-nodes.spec.ts`
- Test: `backend/tests/integration/test_node_offline_recovery.py`

- [ ] **Step 1: Write failing role and one-time-token UI tests**

```ts
test("admin sees enrollment token once", async ({ page }) => {
  await page.goto("/system/runtimes")
  await page.getByRole("button", { name: "安装新节点" }).click()
  await expect(page.getByLabel("安装命令")).toContainText("--enrollment-token")
  await page.reload()
  await expect(page.getByLabel("安装命令")).toHaveCount(0)
})
```

- [ ] **Step 2: Implement node list, detail, revoke, and enrollment UI**

Render online state from server heartbeat age, show versions and last-seen time, and require confirmation for credential revoke. Never cache the raw token in local storage.

- [ ] **Step 3: Run build and acceptance tests**

Run: `cd frontend && npm run build`
Run: `cd frontend && PLAYWRIGHT_NO_WEBSERVER=1 npx playwright test tests/runtime-nodes.spec.ts`
Run: `cd backend && python3 -m pytest tests/integration/test_node_offline_recovery.py -q`
Expected: PASS including simulated one-day shutdown and same-node recovery.

- [ ] **Step 4: Commit**

```bash
git add frontend backend/tests/integration/test_node_offline_recovery.py
git commit -m "feat: complete node enrollment phase"
```
