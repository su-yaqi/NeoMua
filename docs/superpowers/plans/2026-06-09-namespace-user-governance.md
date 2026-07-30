# Namespace User Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete namespace-scoped user governance so `superuser` can manage platform users and namespaces while namespace `admin` can manage only members in their own namespace.

**Architecture:** Keep the existing `user` + `namespace` + `user_namespace_link` model, tighten the backend permission rules around those tables, then wire the frontend to the enhanced platform and namespace member APIs. Reuse the current admin and system pages where possible, adding a focused member-management route instead of inventing a parallel UI shell.

**Tech Stack:** FastAPI, SQLModel, Pytest, React, TanStack Router, TanStack Query, Playwright

---

### Task 1: Tighten backend namespace permission rules

**Files:**
- Modify: `backend/tests/api/routes/test_namespaces.py`
- Modify: `backend/app/crud.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/routes/namespaces.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_namespace_admin_cannot_remove_own_membership(...): ...
def test_namespace_admin_cannot_downgrade_self(...): ...
def test_regular_users_only_receive_active_namespaces(...): ...
def test_namespace_admin_cannot_manage_other_namespace(...): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/api/routes/test_namespaces.py -q`
Expected: FAIL on self-downgrade/self-removal/active-namespace assertions

- [ ] **Step 3: Write minimal implementation**

```python
if not current_user.is_superuser and user_id == current_user.id:
    raise HTTPException(status_code=403, detail="Namespace admins cannot remove or downgrade themselves")

if user.is_superuser:
    return session.exec(select(Namespace).order_by(col(Namespace.name))).all()

statement = statement.where(Namespace.is_active == True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/api/routes/test_namespaces.py -q`
Expected: PASS

### Task 2: Complete backend platform governance endpoints

**Files:**
- Modify: `backend/tests/api/routes/test_namespaces.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/api/routes/namespaces.py`
- Modify: `backend/app/crud.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_create_platform_user_with_namespace_assignments(...): ...
def test_create_namespace_user_reuses_existing_platform_user(...): ...
def test_delete_namespace_membership_keeps_platform_user(...): ...
def test_superuser_can_read_namespace_members_of_inactive_namespace(...): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/api/routes/test_namespaces.py -q`
Expected: FAIL on user-creation payloads or membership behavior

- [ ] **Step 3: Write minimal implementation**

```python
existing = get_user_by_email(...)
if existing:
    ensure_namespace_membership(...)
    return existing

if namespace_in.admin_user_id and not session.get(User, namespace_in.admin_user_id):
    raise HTTPException(status_code=404, detail="Admin user not found")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/api/routes/test_namespaces.py -q`
Expected: PASS

### Task 3: Rebuild the platform governance page around platform APIs

**Files:**
- Modify: `frontend/src/routes/_layout/admin.tsx`
- Modify: `frontend/src/components/Admin/columns.tsx`
- Modify: `frontend/src/components/Admin/UserActionsMenu.tsx`
- Modify: `frontend/src/components/Admin/EditUser.tsx`
- Modify: `frontend/src/components/Admin/DeleteUser.tsx`
- Modify: `frontend/src/components/System/AddPlatformUser.tsx`
- Modify: `frontend/src/client/tenantApi.ts`
- Test: `frontend/tests/admin.spec.ts`

- [ ] **Step 1: Write the failing test**

```ts
test("Admin page shows platform user governance in Chinese and uses namespace-aware data", async ({ page }) => {
  await page.goto("/admin")
  await expect(page.getByRole("heading", { name: "平台用户治理" })).toBeVisible()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx playwright test tests/admin.spec.ts --grep "platform user governance"`
Expected: FAIL because page still renders old copy and data source

- [ ] **Step 3: Write minimal implementation**

```tsx
const { data } = useSuspenseQuery({
  queryKey: ["platform-users"],
  queryFn: tenantApi.readPlatformUsers,
})

<h1>平台用户治理</h1>
<AddPlatformUser />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx playwright test tests/admin.spec.ts`
Expected: PASS

### Task 4: Add namespace member-management UI

**Files:**
- Create: `frontend/src/routes/_layout/system.namespaces.$namespaceId.members.tsx`
- Create: `frontend/src/components/System/NamespaceMembersTable.tsx`
- Create: `frontend/src/components/System/AddNamespaceMember.tsx`
- Create: `frontend/src/components/System/EditNamespaceMember.tsx`
- Create: `frontend/src/components/System/DeleteNamespaceMember.tsx`
- Modify: `frontend/src/components/System/columns.tsx`
- Modify: `frontend/src/components/System/NamespaceActionsMenu.tsx`
- Modify: `frontend/src/client/tenantApi.ts`
- Test: `frontend/tests/admin.spec.ts`

- [ ] **Step 1: Write the failing test**

```ts
test("Superuser can open namespace member management from the namespace list", async ({ page }) => {
  await page.goto("/system/namespaces")
  await expect(page.getByRole("link", { name: "成员管理" }).first()).toBeVisible()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx playwright test tests/admin.spec.ts --grep "member management"`
Expected: FAIL because route and action do not exist

- [ ] **Step 3: Write minimal implementation**

```tsx
<Link to="/system/namespaces/$namespaceId/members" params={{ namespaceId: row.original.id }}>
  成员管理
</Link>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx playwright test tests/admin.spec.ts`
Expected: PASS

### Task 5: Align layout and navigation with the new permission model

**Files:**
- Modify: `frontend/src/routes/_layout.tsx`
- Modify: `frontend/src/components/Sidebar/AppSidebar.tsx`
- Modify: `frontend/src/components/Sidebar/Main.tsx`
- Test: `frontend/tests/admin.spec.ts`

- [ ] **Step 1: Write the failing test**

```ts
test("Non-superuser does not see platform governance navigation", async ({ page }) => {
  await page.goto("/")
  await expect(page.getByText("用户管理")).not.toBeVisible()
  await expect(page.getByText("空间管理")).not.toBeVisible()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx playwright test tests/admin.spec.ts --grep "navigation"`
Expected: FAIL because sidebar still shows namespace management to all logged-in users

- [ ] **Step 3: Write minimal implementation**

```tsx
if (currentUser?.is_superuser) {
  items.push({ title: "空间管理", path: "/system/namespaces" })
  items.push({ title: "用户管理", path: "/admin" })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx playwright test tests/admin.spec.ts`
Expected: PASS

### Task 6: Final verification

**Files:**
- Test: `backend/tests/api/routes/test_namespaces.py`
- Test: `backend/tests/api/routes/test_users.py`
- Test: `frontend/tests/admin.spec.ts`

- [ ] **Step 1: Run focused backend verification**

Run: `python3 -m pytest backend/tests/api/routes/test_namespaces.py backend/tests/api/routes/test_users.py -q`
Expected: PASS

- [ ] **Step 2: Run focused frontend verification**

Run: `cd frontend && npx playwright test tests/admin.spec.ts`
Expected: PASS

- [ ] **Step 3: Run one smoke check for auth + layout**

Run: `cd frontend && npx playwright test tests/login.spec.ts --grep "login"`
Expected: PASS
