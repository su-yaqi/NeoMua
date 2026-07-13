import { expect, test } from "@playwright/test"
import { mockV06Member, v06UserId } from "./utils/v06"

test.use({ storageState: { cookies: [], origins: [] } })

test("project detail renders multiple repositories and exact Spec bindings", async ({
  page,
}) => {
  await mockV06Member(page)
  await page.route("**/api/v1/projects/project-1", (route) =>
    route.fulfill({
      json: {
        id: "project-1",
        name: "NeoMua V0.6",
        slug: "neomua-v06",
        description: "多仓库项目",
        status: "active",
        default_runtime_id: "runtime-1",
        member_ids: [v06UserId],
      },
    }),
  )
  await page.route("**/api/v1/projects/project-1/repositories", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "repo-1",
            remote_url: "https://example.test/frontend.git",
            purpose: "前端应用",
            status: "available",
          },
          {
            id: "repo-2",
            remote_url: "https://example.test/backend.git",
            purpose: "后端服务",
            status: "available",
          },
        ],
        count: 2,
      },
    }),
  )
  await page.route("**/api/v1/projects/project-1/spec-locations", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "spec-1",
            repository_id: "repo-1",
            path: "context/prds/v0.6",
            description: "V0.6 需求",
            location_type: "directory",
            status: "valid",
            binding: { standard_version_id: "standard-version-1" },
          },
        ],
        count: 1,
      },
    }),
  )
  await page.route("**/api/v1/spec-standards", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/projects/project-1/workflow-instances", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )

  await page.goto("/projects/project-1")
  await expect(page.getByRole("heading", { name: "NeoMua V0.6" })).toBeVisible()
  await page.getByRole("tab", { name: "仓库" }).click()
  await expect(
    page.getByText("https://example.test/frontend.git"),
  ).toBeVisible()
  await expect(page.getByText("https://example.test/backend.git")).toBeVisible()
  await page.getByRole("tab", { name: "Spec" }).click()
  await expect(page.getByText("context/prds/v0.6")).toBeVisible()
  await expect(
    page.getByText("standard-version-1", { exact: false }),
  ).toBeVisible()
})
