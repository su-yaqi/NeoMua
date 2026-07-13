import { expect, test } from "@playwright/test"
import { mockV06Member, v06UserId } from "./utils/v06"

test.use({ storageState: { cookies: [], origins: [] } })

test("roundtable shows recovered SSE messages and full delegation timeline", async ({
  page,
}) => {
  await mockV06Member(page)
  await page.route("**/api/v1/conversations/conversation-1", (route) =>
    route.fulfill({
      json: {
        id: "conversation-1",
        title: "圆桌验收",
        mode: "agent",
        visibility: "private",
        status: "active",
        runtime_id: "runtime-0001",
        provider_config_id: null,
        model_id: null,
        project_id: null,
        current_context_snapshot_id: null,
        updated_at: "2030-01-01T00:00:00Z",
        agents: [
          {
            id: "agent-main",
            role: "main",
            agent_id: "main-agent-id",
            agent_release_id: "main-release",
            resolved_spec_digest: "a".repeat(64),
          },
          {
            id: "agent-collaborator",
            role: "collaborator",
            agent_id: "collaborator-agent-id",
            agent_release_id: "collaborator-release",
            resolved_spec_digest: "b".repeat(64),
          },
        ],
      },
    }),
  )
  await page.route("**/api/v1/conversations/conversation-1/messages", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "message-1",
            sequence: 1,
            author_type: "user",
            author_id: v06UserId,
            target_type: "main",
            target_agent_id: null,
            payload: { content: "请主持分析" },
            status: "completed",
            error: null,
            created_at: "2030-01-01T00:00:00Z",
          },
          {
            id: "message-2",
            sequence: 2,
            author_type: "agent",
            author_id: "agent-collaborator",
            target_type: "main",
            target_agent_id: null,
            payload: { conclusion: "协作者完整结果" },
            status: "completed",
            error: null,
            created_at: "2030-01-01T00:00:01Z",
          },
        ],
        count: 2,
      },
    }),
  )
  await page.route(
    "**/api/v1/conversations/conversation-1/delegations",
    (route) =>
      route.fulfill({
        json: {
          data: [
            {
              id: "delegation-1",
              source_message_id: "message-1",
              source_agent_id: "agent-main",
              target_agent_id: "agent-collaborator",
              status: "completed",
              input_payload: { content: "请给出独立结论" },
              result_payload: { conclusion: "协作者完整结果" },
              error: null,
              task_id: "agent-task-1",
              created_at: "2030-01-01T00:00:00Z",
              completed_at: "2030-01-01T00:00:01Z",
            },
          ],
          count: 1,
        },
      }),
  )
  await page.route("**/api/v1/conversations/conversation-1/events", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: `id: 7\nevent: delegation_updated\ndata: {"id":"event-7","conversation_id":"conversation-1","sequence":7,"event_type":"delegation_updated","payload":{"delegation_id":"delegation-1"},"created_at":"2030-01-01T00:00:01Z"}\n\n`,
    }),
  )

  await page.goto("/workspace/conversations/conversation-1")
  await expect(page.getByRole("heading", { name: "圆桌验收" })).toBeVisible()
  await expect(
    page.getByText("协作者完整结果", { exact: false }).first(),
  ).toBeVisible()
  await expect(page.getByText("完整委派时间线", { exact: true })).toBeVisible()
  await expect(page.getByText("Delegation delegation-1")).toBeVisible()
  await expect(page.getByText("Agent Task agent-task-1")).toBeVisible()
})
