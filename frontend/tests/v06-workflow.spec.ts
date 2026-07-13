import { expect, test } from "@playwright/test"
import { mockV06Member } from "./utils/v06"

test.use({ storageState: { cookies: [], origins: [] } })

const task = {
  id: "workflow-task-1",
  project_id: "project-1",
  template_version_id: "template-version-103",
  workflow_slug: "project-delivery",
  application: {
    component_key: "workflow.project_delivery.v1_0_3",
    route_slug: "project-delivery",
    build_digest: "c".repeat(64),
    shell_version: "0.6.0",
  },
  package_digest: "d".repeat(64),
  title: "完成 V0.6 验收",
  status: "running",
  input: { request: "完成全部验收" },
  runtime_resolution: { clarify: "runtime-1", prepare: "runtime-1" },
  project_context_snapshot: { repositories: [{ commit: "a".repeat(40) }] },
  nodes: [
    {
      id: "node-clarify",
      node_key: "clarify",
      status: "waiting_confirmation",
      expected_revision: 0,
      assignee_id: null,
      resolved_runtime_id: "runtime-1",
    },
    {
      id: "node-prepare",
      node_key: "prepare",
      status: "needs_manual_resolution",
      expected_revision: 1,
      assignee_id: null,
      resolved_runtime_id: "runtime-1",
    },
  ],
  created_at: "2030-01-01T00:00:00Z",
  updated_at: "2030-01-01T00:00:00Z",
}

test("exact Workflow component exposes history attachments and recovery actions", async ({
  page,
}) => {
  await mockV06Member(page)
  await page.route("**/api/v1/workflow-instances/workflow-task-1", (route) =>
    route.fulfill({ json: task }),
  )
  await page.route(
    "**/api/v1/workflow-instances/workflow-task-1/events",
    (route) => {
      if (route.request().headers().accept?.includes("text/event-stream")) {
        return route.fulfill({
          contentType: "text/event-stream",
          body: `id: 4\nevent: node_blocked\ndata: {"id":"workflow-event-4","sequence":4,"event_type":"node_blocked","payload":{"node_key":"prepare"},"created_at":"2030-01-01T00:00:00Z"}\n\n`,
        })
      }
      return route.fulfill({
        json: {
          data: [
            {
              id: "workflow-event-1",
              sequence: 1,
              event_type: "task_created",
              payload: {},
              created_at: "2030-01-01T00:00:00Z",
            },
          ],
          count: 1,
        },
      })
    },
  )
  await page.route(
    "**/api/v1/workflow-instances/workflow-task-1/attachments",
    (route) => route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route(
    "**/api/v1/workflow-instances/workflow-task-1/nodes/*",
    (route) => {
      const clarify = route.request().url().endsWith("/clarify")
      return route.fulfill({
        json: {
          node: clarify ? task.nodes[0] : task.nodes[1],
          definition: {
            node_type: clarify ? "human" : "code",
            confirmation_mode: clarify ? "process" : "result",
            skippable: false,
            side_effecting: !clarify,
            input_schema: {},
            output_schema: {},
          },
          revisions: clarify
            ? []
            : [{ revision: 1, output: { summary: "候选交付" } }],
          executions: clarify
            ? []
            : [
                {
                  id: "execution-1",
                  attempt: 1,
                  status: "needs_manual_resolution",
                  error: { code: "lease_expired" },
                  external_state_proof: null,
                },
              ],
          confirmations: [],
          gates: clarify ? [] : [{ gate_type: "entry", passed: true }],
          artifacts: [],
          messages: [],
        },
      })
    },
  )

  await page.goto("/apps/project-delivery/tasks/workflow-task-1")
  await expect(
    page.getByRole("heading", { name: "完成 V0.6 验收" }),
  ).toBeVisible()
  await expect(
    page.getByText("workflow.project_delivery.v1_0_3", { exact: false }),
  ).toBeVisible()
  await expect(
    page.getByText("任务附件", { exact: true }).first(),
  ).toBeVisible()
  await expect(page.getByText("追加式修订").first()).toBeVisible()
  await expect(page.getByText("Gate 结果").first()).toBeVisible()
  await expect(page.getByRole("button", { name: "显式重试" })).toBeVisible()
  await expect(
    page.getByRole("button", { name: "记录证明并允许重试" }),
  ).toBeVisible()
  await expect(
    page.getByText("任务审计事件", { exact: true }).first(),
  ).toBeVisible()
})

test("unknown fixed Workflow component is blocked instead of downgraded", async ({
  page,
}) => {
  await mockV06Member(page)
  await page.route("**/api/v1/workflow-instances/workflow-task-1", (route) =>
    route.fulfill({
      json: {
        ...task,
        application: {
          ...task.application,
          component_key: "workflow.project_delivery.missing",
        },
      },
    }),
  )
  await page.goto("/apps/project-delivery/tasks/workflow-task-1")
  await expect(
    page.getByText("已阻止使用通用页面替代", { exact: false }),
  ).toBeVisible()
})
