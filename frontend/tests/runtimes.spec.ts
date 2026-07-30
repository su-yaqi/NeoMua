import { expect, type Page, test } from "@playwright/test"

const namespaceId = "00000000-0000-0000-0000-000000000010"
const runtimeId = "00000000-0000-0000-0000-000000000030"

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
  await page.route("**/api/v1/runtimes", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: runtimeId,
            namespace_id: namespaceId,
            runtime_node_id: null,
            location_type: "platform",
            management_type: "platform_builtin",
            lifecycle_source_key: "platform:claude-agent-sdk",
            name: "平台 Claude Runtime",
            installation_key: "platform:claude-agent-sdk",
            engine_type: "claude_agent_sdk",
            engine_version: "2.1.191",
            adapter_version: "1.0.0",
            status: "available",
            enabled: true,
            available_model_count: 1,
          },
        ],
        count: 1,
      },
    }),
  )
  await page.route(`**/api/v1/runtimes/${runtimeId}`, (route) =>
    route.fulfill({
      json: {
        id: runtimeId,
        configurations: [
          {
            id: "00000000-0000-0000-0000-000000000031",
            revision: 1,
            origin: "system_builtin",
            adapter_execution_ref: "platform:claude-agent-sdk",
            executable: "claude",
            arguments: [],
            environment_allowlist: [],
            working_directory_policy: "workspace",
            status: "applied",
          },
        ],
        capability_reports: [],
      },
    }),
  )
  await page.route(`**/api/v1/runtimes/${runtimeId}/model-bindings`, (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/llm/model-definitions", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
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
    await page.route("**/api/v1/runtimes/nodes/bootstrap-sessions", (route) => {
      if (route.request().method() === "GET") return route.fulfill({ json: [] })
      return route.fulfill({
        status: 201,
        json: {
          id: "00000000-0000-0000-0000-000000000050",
          token: "nmenr_once",
          expires_at: "2030-01-01T00:00:00Z",
          management_mode: "service",
          bootstrap_session_id: "00000000-0000-0000-0000-000000000051",
          release_channel: "stable",
          status: "waiting_for_install",
          signing_public_key: "dGVzdC1zaWduaW5nLXB1YmxpYy1rZXk=",
        },
      })
    })
    await page.goto("/system/runtimes")
    await expect(
      page.getByRole("heading", { name: "运行时管理" }),
    ).toBeVisible()
    await expect(page.getByText("平台 Claude Runtime")).toBeVisible()
    await expect(page.getByText("无需配置。请在“大模型接入配置”")).toBeVisible()
    await expect(
      page.getByRole("button", { name: "创建平台 Runtime" }),
    ).toHaveCount(0)
    await page.getByRole("button", { name: "查看状态与模型" }).click()
    await expect(
      page.getByRole("button", { name: "保存并应用新修订" }),
    ).toHaveCount(0)
    await expect(page.getByText("系统配置证据（只读）")).toBeVisible()
    await expect(page.getByRole("button", { name: "上传内容" })).toBeVisible()
    await page.getByRole("button", { name: "添加服务节点" }).click()
    await page.getByRole("button", { name: "生成一次性安装凭证" }).click()
    const command = page.getByRole("region", { name: "添加服务节点命令" })
    await expect(command).toContainText("neomua-node install --mode service")
    await expect(command).toContainText("--signing-public-key")
    await expect(command).not.toContainText("nmenr_once")
    await expect(
      page.getByRole("region", { name: "一次性凭证" }),
    ).toContainText("nmenr_once")
  })

  test("developer reads RuntimeInstance inventory but cannot mutate it", async ({
    page,
  }) => {
    await mockRuntimePage(page, "developer")
    await page.goto("/system/runtimes")
    await expect(page.getByText("平台 Claude Runtime")).toBeVisible()
    await expect(
      page.getByRole("button", { name: "创建平台 Runtime" }),
    ).toHaveCount(0)
    await page.getByRole("button", { name: "查看状态与模型" }).click()
    await expect(
      page.getByRole("button", { name: "保存并应用新修订" }),
    ).toHaveCount(0)
    await expect(page.getByRole("button", { name: "声明绑定" })).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: "添加服务节点" }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: "添加客户端节点" }),
    ).toHaveCount(0)
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
