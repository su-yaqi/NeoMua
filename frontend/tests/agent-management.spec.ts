import { expect, type Page, test } from "@playwright/test"

const namespaceId = "00000000-0000-0000-0000-000000000010"
const sourceAgent = {
  id: "00000000-0000-0000-0000-000000000020",
  namespace_id: namespaceId,
  slug: "source-agent",
  name: "Source Agent",
  description: "source description",
  status: "active",
  created_at: "2030-01-01T00:00:00Z",
  updated_at: "2030-01-01T00:00:00Z",
  draft_revision: 2,
  validation_status: "validated",
  harness_type: "claude_code",
  model_id: "claude-test",
}

async function mockAgentManagement(
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
        id: "00000000-0000-0000-0000-000000000030",
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
  await page.route("**/api/v1/agents", (route) =>
    route.fulfill({ json: { data: [sourceAgent], count: 1 } }),
  )
  await page.route("**/api/v1/harness-profiles", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 201,
        json: {
          id: "00000000-0000-0000-0000-000000000040",
          namespace_id: namespaceId,
          name: "Playwright Profile",
          harness_type: "claude_code",
          config_schema_version: "1.0",
          cli_version_constraint: ">=1.0.0",
          sdk_version_constraint: ">=0.2.0",
          config: {},
          archived: false,
          referenced_by_agents: false,
          target_compatibility: [],
          created_at: "2030-01-01T00:00:00Z",
          updated_at: "2030-01-01T00:00:00Z",
        },
      })
    }
    return route.fulfill({ json: { data: [], count: 0 } })
  })
  await page.route("**/api/v1/harnesses/environment-catalog", (route) =>
    route.fulfill({
      json: {
        allowlist: ["ANTHROPIC_MODEL"],
        reserved: [],
        denylist: ["PATH"],
      },
    }),
  )
}

test.describe("Agent and Harness management roles", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("admin can create a Harness Profile", async ({ page }) => {
    await mockAgentManagement(page, "admin")
    await page.goto("/system/harnesses")
    await page.getByRole("button", { name: "创建 Profile" }).click()
    await page
      .getByRole("dialog")
      .locator("input")
      .first()
      .fill("Playwright Profile")
    const requestPromise = page.waitForRequest(
      (request) =>
        request.url().endsWith("/api/v1/harness-profiles") &&
        request.method() === "POST",
    )
    await page.getByRole("button", { name: "创建", exact: true }).click()
    const request = await requestPromise
    expect(request.postDataJSON()).toMatchObject({
      name: "Playwright Profile",
      config: {
        permission_mode: "default",
        working_directory_strategy: "inherit",
      },
    })
    await expect(page.getByText("Profile 已创建")).toBeVisible()
  })

  test("admin copies an Agent with an explicit new name and slug", async ({
    page,
  }) => {
    await mockAgentManagement(page, "admin")
    await page.route(`**/api/v1/agents/${sourceAgent.id}/copy`, (route) =>
      route.fulfill({
        status: 201,
        json: {
          ...sourceAgent,
          id: "00000000-0000-0000-0000-000000000050",
          slug: "copied-agent",
          name: "Copied Agent",
        },
      }),
    )
    await page.route(
      "**/api/v1/agents/00000000-0000-0000-0000-000000000050",
      (route) =>
        route.fulfill({
          json: { ...sourceAgent, id: "00000000-0000-0000-0000-000000000050" },
        }),
    )
    await page.route(
      "**/api/v1/agents/00000000-0000-0000-0000-000000000050/draft",
      (route) =>
        route.fulfill({
          json: {
            agent_id: "00000000-0000-0000-0000-000000000050",
            revision: 1,
            harness_profile_id: null,
            provider_config_id: null,
            model_id: null,
            system_prompt: "",
            config: {},
            validated_revision: null,
            validation_result: null,
            validation_status: "unvalidated",
            updated_at: "2030-01-01T00:00:00Z",
          },
        }),
    )
    await page.route("**/api/v1/llm/provider-configs", (route) =>
      route.fulfill({ json: { data: [], count: 0 } }),
    )

    await page.goto("/system/agents")
    await page.getByRole("button", { name: "复制" }).click()
    const dialog = page.getByRole("dialog")
    await dialog.locator("input").nth(0).fill("copied-agent")
    await dialog.locator("input").nth(1).fill("Copied Agent")
    const requestPromise = page.waitForRequest((request) =>
      request.url().endsWith(`/agents/${sourceAgent.id}/copy`),
    )
    await page.getByRole("button", { name: "复制", exact: true }).last().click()
    const request = await requestPromise
    expect(request.postDataJSON()).toEqual({
      slug: "copied-agent",
      name: "Copied Agent",
    })
  })

  test("developer is read-only and user cannot see management pages", async ({
    page,
  }) => {
    await mockAgentManagement(page, "developer")
    await page.goto("/system/agents")
    await expect(
      page.getByRole("heading", { name: "Agent 管理" }),
    ).toBeVisible()
    await expect(page.getByRole("button", { name: "创建 Agent" })).toHaveCount(
      0,
    )
    await expect(page.getByRole("button", { name: "复制" })).toHaveCount(0)

    await mockAgentManagement(page, "user")
    await page.goto("/system/agents")
    await expect(page.getByRole("heading", { name: "Agent 管理" })).toHaveCount(
      0,
    )
    await expect(page.getByText("Agent 管理", { exact: true })).toHaveCount(0)
  })

  test("admin must pass server precheck before creating an activation", async ({
    page,
  }) => {
    await mockAgentManagement(page, "admin")
    const releaseId = "00000000-0000-0000-0000-000000000060"
    const runtimeId = "00000000-0000-0000-0000-000000000061"
    const activationId = "00000000-0000-0000-0000-000000000062"
    await page.route(`**/api/v1/agent-releases/${releaseId}`, (route) =>
      route.fulfill({
        json: {
          id: releaseId,
          version: "1.0.0",
          resolved_spec_digest: "a".repeat(64),
          manifest: { schema_version: "1.0" },
          dependency_lock: {},
          components: [],
          manifest_digest: "b".repeat(64),
          signature: "signature",
          signing_public_key: "public-key",
          signature_valid: true,
        },
      }),
    )
    await page.route("**/api/v1/runtimes/platform", (route) =>
      route.fulfill({
        json: {
          id: runtimeId,
          harness_capabilities: { claude_code: { cli_version: "2.1.191" } },
        },
      }),
    )
    await page.route("**/api/v1/runtimes/nodes", (route) =>
      route.fulfill({ json: { data: [], count: 0 } }),
    )
    await page.route(
      `**/api/v1/agent-releases/${releaseId}/activations/precheck`,
      (route) =>
        route.fulfill({
          json: {
            release_id: releaseId,
            compatible: true,
            targets: [{ runtime_profile_id: runtimeId, status: "pending" }],
          },
        }),
    )
    await page.route(
      `**/api/v1/agent-releases/${releaseId}/activations`,
      (route) =>
        route.fulfill({
          status: 202,
          json: { id: activationId, status: "deploying", deployments: [] },
        }),
    )
    await page.route(`**/api/v1/agent-activations/${activationId}`, (route) =>
      route.fulfill({
        json: { id: activationId, status: "deploying", deployments: [] },
      }),
    )

    await page.goto(`/system/agents/${sourceAgent.id}/releases/${releaseId}`)
    const activate = page.getByRole("button", { name: "创建激活批次" })
    await expect(activate).toBeDisabled()
    await page.getByRole("checkbox", { name: /^平台运行时/ }).check()
    const precheckRequest = page.waitForRequest((request) =>
      request.url().endsWith("/activations/precheck"),
    )
    await page.getByRole("button", { name: "运行目标预检" }).click()
    expect((await precheckRequest).postDataJSON()).toEqual({
      runtime_profile_ids: [runtimeId],
    })
    await expect(activate).toBeEnabled()
    await activate.click()
    await expect(page).toHaveURL(
      new RegExp(`/system/agent-activations/${activationId}$`),
    )
  })

  test("admin can retry an explicitly failed activation target", async ({
    page,
  }) => {
    await mockAgentManagement(page, "admin")
    const activationId = "00000000-0000-0000-0000-000000000070"
    const deploymentId = "00000000-0000-0000-0000-000000000071"
    await page.route(`**/api/v1/agent-activations/${activationId}`, (route) =>
      route.fulfill({
        json: {
          id: activationId,
          status: "failed",
          deployments: [
            {
              id: deploymentId,
              runtime_profile_id: "00000000-0000-0000-0000-000000000072",
              attempt: 1,
              status: "failed",
              error: { code: "target_failed" },
            },
          ],
        },
      }),
    )
    await page.route(
      `**/api/v1/agent-deployments/${deploymentId}/retry`,
      (route) => route.fulfill({ status: 202, json: { id: deploymentId } }),
    )
    page.on("dialog", (dialog) => dialog.accept())

    await page.goto(`/system/agent-activations/${activationId}`)
    const retryRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/agent-deployments/${deploymentId}/retry`),
    )
    await page.getByRole("button", { name: "Retry" }).click()
    await retryRequest
    await expect(page.getByText("部署重试已创建")).toBeVisible()
  })

  test("MCP detail exposes target validation tools and runtime actions", async ({
    page,
  }) => {
    await mockAgentManagement(page, "admin")
    const serverId = "00000000-0000-0000-0000-000000000080"
    const revisionId = "00000000-0000-0000-0000-000000000081"
    const targetId = "00000000-0000-0000-0000-000000000082"
    const runtimeId = "00000000-0000-0000-0000-000000000083"
    await page.route(`**/api/v1/mcp-servers/${serverId}`, (route) =>
      route.fulfill({
        json: {
          id: serverId,
          slug: "managed-mcp",
          name: "Managed MCP",
          revisions: [
            {
              id: revisionId,
              revision: 1,
              transport: "streamable_http",
              config: { endpoint: "https://mcp.example" },
            },
          ],
        },
      }),
    )
    await page.route(`**/api/v1/mcp-servers/${serverId}/revisions/1`, (route) =>
      route.fulfill({
        json: {
          id: revisionId,
          revision: 1,
          transport: "streamable_http",
          config: { endpoint: "https://mcp.example" },
          targets: [
            {
              id: targetId,
              runtime_profile_id: runtimeId,
              status: "verified",
            },
          ],
        },
      }),
    )
    await page.route(`**/api/v1/mcp-targets/${targetId}/validations`, (route) =>
      route.fulfill({
        json: {
          data: [
            {
              status: "verified",
              tools: [{ qualified_name: "mcp:managed-mcp:search" }],
            },
          ],
          count: 1,
        },
      }),
    )
    await page.route(`**/api/v1/mcp-targets/${targetId}/runtime`, (route) =>
      route.fulfill({
        json: { instance: { status: "ready", generation: 1 }, events: [] },
      }),
    )
    await page.route(`**/api/v1/mcp-targets/${targetId}/validate`, (route) =>
      route.fulfill({ status: 202, json: { id: "attempt-1" } }),
    )
    await page.route("**/api/v1/runtimes/platform", (route) =>
      route.fulfill({ json: { id: runtimeId } }),
    )
    await page.route("**/api/v1/runtimes/nodes", (route) =>
      route.fulfill({ json: { data: [], count: 0 } }),
    )

    await page.goto(`/system/mcp-servers/${serverId}`)
    await expect(
      page.getByRole("heading", { name: "Managed MCP" }),
    ).toBeVisible()
    await page.getByText("校验历史与 Tool 快照", { exact: true }).click()
    await expect(page.getByText("mcp:managed-mcp:search")).toBeVisible()
    const validationRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/mcp-targets/${targetId}/validate`),
    )
    await page.getByRole("button", { name: "校验并发现 Tools" }).click()
    await validationRequest
  })
})
