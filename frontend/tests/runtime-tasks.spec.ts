import { expect, test } from "@playwright/test"

test.use({ storageState: { cookies: [], origins: [] } })

test("interrupted task offers explicit retry only", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      "selected_namespace_id",
      "00000000-0000-0000-0000-000000000010",
    )
  })
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "00000000-0000-0000-0000-000000000020",
        email: "developer@example.com",
        is_active: true,
        namespace_roles: [
          {
            namespace_id: "00000000-0000-0000-0000-000000000010",
            role: "developer",
          },
        ],
      },
    }),
  )
  await page.route("**/api/v1/namespaces/mine", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "00000000-0000-0000-0000-000000000010",
            name: "测试空间",
            code: "test",
            is_active: true,
          },
        ],
        count: 1,
      },
    }),
  )
  await page.route("**/api/v1/runtime-tasks/task-1", (route) =>
    route.fulfill({
      json: {
        id: "task-1",
        runtime_profile_id: "runtime-1",
        node_id: "node-1",
        task_kind: "ordinary",
        status: "interrupted",
        prompt: "work",
        snapshot: {},
        final_result: null,
        retry_of_task_id: null,
        created_at: "2030-01-01T00:00:00Z",
        updated_at: "2030-01-01T00:00:00Z",
      },
    }),
  )
  await page.route("**/api/v1/runtimes/tasks/task-1/stream", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }),
  )
  await page.goto("/system/runtimes/tasks/task-1")
  await expect(page.getByText("状态：interrupted")).toBeVisible()
  await expect(page.getByRole("button", { name: "重新执行" })).toBeVisible()
  await expect(page.getByText("自动重试中")).toHaveCount(0)
})
