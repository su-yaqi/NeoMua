import { expect, test } from "@playwright/test"
import { mockV06Member } from "./utils/v06"

test.use({ storageState: { cookies: [], origins: [] } })

const workflowCatalog = {
  data: [
    {
      id: "template-web-platform",
      slug: "web-platform-development",
      name: "Web 平台开发流",
      description: "从需求沟通到上线的 Web 平台开发流程",
      scope_type: "platform",
      application: {
        route_slug: "web-platform-development",
        component_key: "workflow.web_platform_development.v1_0_1",
      },
      versions: [
        {
          version: {
            id: "version-web-platform-101",
            version: "1.0.1",
            package_digest: "d".repeat(64),
            manifest: { project_mode: "required" },
          },
          application: {
            route_slug: "web-platform-development",
            component_key: "workflow.web_platform_development.v1_0_1",
            build_digest: "b".repeat(64),
            shell_version: "0.7.0",
          },
          enablement: { enabled: true, is_default: true },
          execution_configuration: {
            revision_id: "configuration-revision-1",
            revision: 1,
            project_id: "project-1",
            bindings: [],
          },
        },
      ],
    },
  ],
  count: 1,
}

test("AI workspace keeps the familiar conversation layout and only Chat or Agent modes", async ({
  page,
}) => {
  await mockV06Member(page)
  await page.route("**/api/v1/conversations", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/projects?*", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/conversation-catalog/runtimes", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )

  await page.goto("/workspace")

  await expect(page.getByRole("link", { name: "新会话" })).toBeVisible()
  await expect(page.getByText("暂无历史会话。", { exact: true })).toBeVisible()
  await expect(page.getByText("开始一个新对话", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Chat" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Agent" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Workflow" })).toHaveCount(0)
  await expect(page.getByPlaceholder("输入消息…")).toBeVisible()
})

test("Workflow opens as an application directory and application instance list", async ({
  page,
}) => {
  await mockV06Member(page)
  await page.route("**/api/v1/workflow-templates", (route) =>
    route.fulfill({ json: workflowCatalog }),
  )
  await page.route("**/api/v1/workflow-instances?template_id=*", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )

  await page.goto("/workflows")
  await expect(page.getByRole("heading", { name: "Workflow" })).toBeVisible()
  await expect(page.getByText("Web 平台开发流", { exact: true })).toBeVisible()
  await page.getByText("Web 平台开发流", { exact: true }).click()

  await expect(page.getByRole("link", { name: "新建流程实例" })).toBeVisible()
  await expect(page.getByText("流程实例", { exact: true })).toBeVisible()
  await expect(
    page.getByText("当前应用还没有流程实例。", { exact: true }),
  ).toBeVisible()
})

test("Workflow instance creation only asks for task name and what to do", async ({
  page,
}) => {
  await mockV06Member(page)
  await page.route("**/api/v1/workflow-templates", (route) =>
    route.fulfill({ json: workflowCatalog }),
  )

  await page.goto("/apps/web-platform-development/new")

  await expect(
    page.getByRole("heading", { name: "新建 Web 平台开发任务" }),
  ).toBeVisible()
  await expect(page.getByLabel("任务名称")).toBeVisible()
  await expect(page.getByLabel("要做什么")).toBeVisible()
  await expect(page.getByText("选择项目", { exact: false })).toHaveCount(0)
  await expect(page.getByText("选择运行", { exact: false })).toHaveCount(0)
  await expect(page.getByText("选择 Agent", { exact: false })).toHaveCount(0)
})
