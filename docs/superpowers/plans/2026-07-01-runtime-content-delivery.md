# Runtime Content Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver immutable signed Agent/Skill/MCP/CLI/workspace content to allowlisted node directories with atomic application and explicit rollback.

**Architecture:** FastAPI validates and signs manifests and stores immutable blobs through a storage abstraction. Node Daemon independently revalidates signature, hashes, paths, sizes, scope, and version before a same-filesystem atomic switch; no artifact action executes commands.

**Tech Stack:** FastAPI, SQLModel/PostgreSQL, SHA-256, Ed25519 signatures, local durable volume with S3-compatible storage interface, Node Daemon, pytest, React/Playwright.

---

### Task 1: Implement manifest validation and signature primitives

**Files:**
- Create: `backend/app/runtime/artifacts/__init__.py`
- Create: `backend/app/runtime/artifacts/manifest.py`
- Create: `backend/app/runtime/artifacts/signing.py`
- Test: `backend/tests/runtime/artifacts/test_manifest.py`
- Test: `backend/tests/runtime/artifacts/test_signing.py`

- [ ] **Step 1: Write failing traversal, symlink, size, and signature tests**

```python
@pytest.mark.parametrize("path", ["/etc/passwd", "../escape", "skills/../../escape"])
def test_rejects_unsafe_manifest_path(path):
    with pytest.raises(UnsafeArtifactPath):
        validate_relative_path(path)

def test_modified_manifest_fails_signature(signer, manifest):
    signature = signer.sign(manifest.canonical_bytes())
    manifest.files[0].sha256 = "0" * 64
    assert not signer.verify(manifest.canonical_bytes(), signature)
```

- [ ] **Step 2: Implement canonical manifest and target policy**

```python
class ArtifactKind(str, Enum):
    AGENT = "agent"
    SKILL = "skill"
    MCP = "mcp"
    CLI_CONFIG = "cli_config"
    WORKSPACE_CONTENT = "workspace_content"
```

Normalize POSIX relative paths, reject absolute paths/`..`/empty segments, reject archive symlinks, enforce per-file and total limits, and sign canonical JSON with a dedicated artifact key.

- [ ] **Step 3: Run primitive tests**

Run: `cd backend && python3 -m pytest tests/runtime/artifacts -q`
Expected: PASS for valid manifests and all escape/tamper cases.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime/artifacts backend/tests/runtime/artifacts
git commit -m "feat: add signed artifact manifests"
```

### Task 2: Add immutable artifact, release, and deployment persistence

**Files:**
- Modify: `backend/app/runtime/models.py`
- Create: `backend/app/runtime/artifacts/repository.py`
- Create: `backend/app/alembic/versions/7c715d4e2f43_add_runtime_artifacts.py`
- Test: `backend/tests/runtime/artifacts/test_repository.py`

- [ ] **Step 1: Write failing immutability and state tests**

```python
def test_artifact_content_cannot_be_replaced(repository, artifact):
    with pytest.raises(ArtifactImmutable):
        repository.replace_blob(artifact.id, b"different")

def test_failed_deployment_keeps_previous_version(service, installed):
    service.fail(installed.deployment_id, "signature_invalid")
    assert installed.node.current_artifact_id == installed.previous_artifact_id
```

- [ ] **Step 2: Implement models and explicit deployment transitions**

Create `runtime_artifact`, `artifact_release`, and `artifact_deployment`; store content hash, signature, kind, storage key, desired/current/previous versions, timestamps, actor and structured error. Disallow mutation of a persisted artifact's hash/storage key.

- [ ] **Step 3: Migrate and test**

Run: `cd backend && alembic revision --rev-id 7c715d4e2f43 --autogenerate -m "add runtime artifacts" && alembic upgrade head && python3 -m pytest tests/runtime/artifacts/test_repository.py -q`
Expected: PASS with namespace cascade and no unrelated schema changes.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime/models.py backend/app/runtime/artifacts backend/app/alembic backend/tests/runtime/artifacts
git commit -m "feat: add artifact release persistence"
```

### Task 3: Build storage abstraction and upload APIs

**Files:**
- Create: `backend/app/runtime/artifacts/storage.py`
- Create: `backend/app/runtime/artifacts/local_storage.py`
- Create: `backend/app/runtime/artifacts/s3_storage.py`
- Create: `backend/app/api/routes/runtime_artifacts.py`
- Modify: `backend/app/api/main.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/api/routes/test_runtime_artifacts.py`

- [ ] **Step 1: Write failing admin-only and immutability API tests**

```python
def test_developer_cannot_upload_artifact(client, developer_headers, artifact_zip):
    response = client.post("/api/v1/runtime-artifacts", headers=developer_headers,
        files={"file": ("skill.zip", artifact_zip, "application/zip")})
    assert response.status_code == 403
```

- [ ] **Step 2: Implement storage interface and streamed upload**

```python
class ArtifactStorage(Protocol):
    async def put_once(self, key: str, source: AsyncIterator[bytes]) -> StoredObject: ...
    async def open(self, key: str) -> AsyncIterator[bytes]: ...
    def issue_download(self, key: str, expires_in: timedelta) -> str: ...
```

Use a durable local volume in local Compose and an S3-compatible implementation for production. Stream to a temporary object while hashing, validate archive entries, then finalize under the hash key. Do not buffer whole archives in memory.

- [ ] **Step 3: Implement create/list/detail APIs**

Admin-only upload accepts artifact kind and logical target, returns immutable version metadata, and never accepts a physical absolute path. Download URLs expire and are scoped to one deployment/node.

- [ ] **Step 4: Run tests**

Run: `cd backend && python3 -m pytest tests/api/routes/test_runtime_artifacts.py -q`
Expected: PASS for streamed upload, duplicate-content reuse, role checks, path rejection and expired download.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime/artifacts backend/app/api/routes/runtime_artifacts.py backend/app/core/config.py backend/tests
git commit -m "feat: add immutable artifact storage API"
```

### Task 4: Implement release protocol and offline-node policy

**Files:**
- Modify: `backend/app/api/routes/runtime_artifacts.py`
- Modify: `backend/app/api/routes/node_socket.py`
- Create: `backend/app/runtime/artifacts/service.py`
- Create: `node_runtime/node_runtime/artifacts/protocol.py`
- Test: `backend/tests/runtime/artifacts/test_release.py`

- [ ] **Step 1: Write failing offline and stale-release tests**

```python
def test_offline_node_does_not_auto_apply_stale_release(service, offline_node, artifact, clock):
    release = service.create_release(artifact.id, [offline_node.id], valid_for=timedelta(hours=1))
    clock.advance(hours=2)
    commands = service.commands_for_reconnected_node(offline_node.id)
    assert commands == []
    assert release.deployments[0].status == DeploymentStatus.EXPIRED
```

- [ ] **Step 2: Implement create/retry/rollback release APIs**

Every release targets nodes in the same namespace and has a validity window. Dispatch `deploy(release_id, deployment_id, manifest, download_url)`. Retry creates a new deployment attempt; rollback targets a previously applied artifact and remains an audited release.

- [ ] **Step 3: Run release tests**

Run: `cd backend && python3 -m pytest tests/runtime/artifacts/test_release.py -q`
Expected: PASS; offline deployment waits, expired release is not auto-applied, and cross-namespace targets are rejected.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime/artifacts backend/app/api node_runtime/node_runtime/artifacts/protocol.py backend/tests
git commit -m "feat: add controlled artifact release protocol"
```

### Task 5: Implement node staging, atomic switch, and recovery

**Files:**
- Create: `node_runtime/node_runtime/artifacts/validator.py`
- Create: `node_runtime/node_runtime/artifacts/installer.py`
- Create: `node_runtime/node_runtime/artifacts/state.py`
- Test: `node_runtime/tests/artifacts/test_validator.py`
- Test: `node_runtime/tests/artifacts/test_installer.py`

- [ ] **Step 1: Write failing tamper and power-loss tests**

```python
def test_power_loss_before_switch_preserves_current_version(harness):
    harness.install("v1")
    harness.begin_install("v2")
    harness.simulate_power_loss()
    harness.restart()
    assert harness.current_version == "v1"
    assert not harness.staging_exists("v2")
```

- [ ] **Step 2: Implement independent validation**

Recheck signature, manifest hash, every file hash/size, logical target, namespace/node scope and symlink absence. Map logical roots from local config only; ignore any physical path supplied by the platform.

- [ ] **Step 3: Implement atomic apply**

Extract into a same-filesystem staging directory with exclusive creation, fsync files/directories, rename current pointer atomically, then report `applied`. On startup remove uncommitted staging directories and preserve the current pointer. No hook or subprocess execution exists in this package.

- [ ] **Step 4: Run node artifact tests**

Run: `cd node_runtime && python3 -m pytest tests/artifacts -q`
Expected: PASS for tamper, traversal, symlink, full disk, power loss, atomic switch and rollback.

- [ ] **Step 5: Commit**

```bash
git add node_runtime/node_runtime/artifacts node_runtime/tests/artifacts
git commit -m "feat: apply node artifacts atomically"
```

### Task 6: Add artifact UI and v0.4 full acceptance

**Files:**
- Create: `frontend/src/routes/_layout/system.runtimes.artifacts.tsx`
- Create: `frontend/src/components/Runtimes/ArtifactUploadDialog.tsx`
- Create: `frontend/src/components/Runtimes/ArtifactReleaseDialog.tsx`
- Create: `frontend/src/routes/_layout/system.runtimes.releases.$releaseId.tsx`
- Modify: `frontend/src/client/tenantApi.ts`
- Test: `frontend/tests/runtime-artifacts.spec.ts`
- Test: `backend/tests/integration/test_artifact_delivery_e2e.py`
- Create: `context/changelogs/v0.4.md`
- Modify: `context/project.md`
- Modify: `context/architecture.md`
- Modify: `context/data-schema.md`
- Modify: `context/apis.md`
- Modify: `context/ui.md`
- Create: `context/modules/runtime_management/api.md`
- Create: `context/modules/runtime_management/flows.md`
- Create: `context/modules/runtime_management/ui.md`

- [ ] **Step 1: Write failing admin-only release and rollback UI tests**

```ts
test("developer cannot see content delivery controls", async ({ page }) => {
  await page.goto("/system/runtimes")
  await expect(page.getByRole("button", { name: "发布内容" })).toHaveCount(0)
})
```

- [ ] **Step 2: Implement artifact list, upload, release detail, retry, and rollback UI**

Show manifest, immutable hash/version, target kind, per-node status and errors. Require confirmation for release and rollback. Never accept a physical node path in any form.

- [ ] **Step 3: Run full acceptance suite**

Run: `cd backend && python3 -m pytest tests -q`
Run: `cd runtime_worker && python3 -m pytest tests -q`
Run: `cd model_gateway && python3 -m pytest tests -q`
Run: `cd node_runtime && python3 -m pytest tests -q`
Run: `cd frontend && npm run build && PLAYWRIGHT_NO_WEBSERVER=1 npx playwright test`
Run: `docker compose config`
Expected: all PASS; no artifact path escape or command execution route exists.

- [ ] **Step 4: Update current-state context only after acceptance**

Record the implemented tables, APIs, page tree, service topology, exact deviations, and completed v0.4 changelog. Do not mark unimplemented behavior complete.

- [ ] **Step 5: Commit**

```bash
git add frontend backend/tests/integration context
git commit -m "feat: complete controlled runtime content delivery"
```
