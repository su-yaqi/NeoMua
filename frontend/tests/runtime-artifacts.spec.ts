import { expect, type Page, test } from "@playwright/test"

const namespaceId = "00000000-0000-0000-0000-000000000010"

async function mockReleasePage(page: Page, role: "admin" | "developer") {
  await page.addInitScript(
    ({ namespaceId }) => {
      localStorage.setItem("selected_namespace_id", namespaceId)
    },
    { namespaceId },
  )
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "00000000-0000-0000-0000-000000000020",
        email: `${role}@example.com`,
        is_active: true,
        is_superuser: false,
        namespace_roles: [{ namespace_id: namespaceId, role }],
      },
    }),
  )
  await page.route("**/api/v1/namespaces/mine", (route) =>
    route.fulfill({
      json: {
        data: [
          { id: namespaceId, name: "测试空间", code: "test", is_active: true },
        ],
        count: 1,
      },
    }),
  )
  await page.route("**/api/v1/runtime-artifacts/releases/release-1", (route) =>
    route.fulfill({
      json: {
        id: "release-1",
        artifact_id: "artifact-1",
        valid_until: "2030-01-01T00:00:00Z",
        rollback_of_release_id: null,
        deployments: [
          {
            id: "deployment-failed",
            node_id: "node-1",
            artifact_id: "artifact-1",
            previous_artifact_id: null,
            attempt: 1,
            status: "failed",
            error: { code: "download_failed" },
          },
          {
            id: "deployment-applied",
            node_id: "node-2",
            artifact_id: "artifact-1",
            previous_artifact_id: "artifact-0",
            attempt: 1,
            status: "applied",
            error: null,
          },
        ],
      },
    }),
  )
}

test.use({ storageState: { cookies: [], origins: [] } })

test("admin can retry and roll back content deployments", async ({ page }) => {
  await mockReleasePage(page, "admin")
  await page.goto("/system/runtimes/releases/release-1")
  await expect(page.getByRole("button", { name: "重试部署" })).toBeVisible()
  await expect(page.getByRole("button", { name: "回滚版本" })).toBeVisible()
})

test("developer sees deployment status without management actions", async ({
  page,
}) => {
  await mockReleasePage(page, "developer")
  await page.goto("/system/runtimes/releases/release-1")
  await expect(page.getByText("状态：failed")).toBeVisible()
  await expect(page.getByRole("button", { name: "重试部署" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "回滚版本" })).toHaveCount(0)
})
