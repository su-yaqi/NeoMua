import { expect, type Page, test } from "@playwright/test"

const namespaceId = "00000000-0000-0000-0000-000000000010"

async function mockRuntimePage(
  page: Page,
  role: "admin" | "developer" | "user",
) {
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
  await page.route("**/api/v1/runtimes/platform", (route) =>
    route.fulfill({
      json: {
        id: "00000000-0000-0000-0000-000000000030",
        namespace_id: namespaceId,
        route_mode: "platform_gateway",
        model_id: "claude-test",
        provider_config_id: "00000000-0000-0000-0000-000000000040",
        base_url: null,
        permission_mode: "default",
        secret_masked: null,
        compatibility_verified: true,
      },
    }),
  )
  await page.route("**/api/v1/llm/provider-configs", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/runtimes/nodes", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/runtime-artifacts", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
}

test.describe("runtime namespace roles", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("admin manages runtime nodes and content", async ({ page }) => {
    await mockRuntimePage(page, "admin")
    await page.route("**/api/v1/runtimes/nodes/enrollment-tokens", (route) =>
      route.fulfill({
        status: 201,
        json: {
          id: "00000000-0000-0000-0000-000000000050",
          token: "nmenr_once",
          expires_at: "2030-01-01T00:00:00Z",
        },
      }),
    )
    await page.goto("/system/runtimes")
    await expect(
      page.getByRole("heading", { name: "运行时管理" }),
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "配置", exact: true }),
    ).toBeVisible()
    await expect(page.getByRole("button", { name: "上传内容" })).toBeVisible()
    await page.getByRole("button", { name: "安装新节点" }).click()
    await expect(page.getByRole("region", { name: "安装命令" })).toContainText(
      "--enrollment-token 'nmenr_once'",
    )
  })

  test("developer can test but cannot configure or distribute", async ({
    page,
  }) => {
    await mockRuntimePage(page, "developer")
    await page.goto("/system/runtimes")
    await expect(page.getByRole("button", { name: "功能测试" })).toBeVisible()
    await expect(
      page.getByRole("button", { name: "配置", exact: true }),
    ).toHaveCount(0)
    await expect(page.getByRole("button", { name: "安装新节点" })).toHaveCount(
      0,
    )
    await expect(page.getByRole("button", { name: "上传内容" })).toHaveCount(0)
  })

  test("user cannot see runtime navigation or page", async ({ page }) => {
    await mockRuntimePage(page, "user")
    await page.goto("/system/runtimes")
    await expect(page.getByRole("heading", { name: "运行时管理" })).toHaveCount(
      0,
    )
    await expect(page.getByRole("link", { name: "运行时管理" })).toHaveCount(0)
  })
})
