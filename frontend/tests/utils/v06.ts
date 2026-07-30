import type { Page } from "@playwright/test"

export const v06NamespaceId = "00000000-0000-0000-0000-000000000610"
export const v06UserId = "00000000-0000-0000-0000-000000000611"

export async function mockV06Member(page: Page) {
  await page.addInitScript(
    ({ namespaceId }) => {
      localStorage.setItem("selected_namespace_id", namespaceId)
    },
    { namespaceId: v06NamespaceId },
  )
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: v06UserId,
        email: "v06@example.com",
        full_name: "V0.6 Member",
        is_active: true,
        is_superuser: false,
        namespace_roles: [{ namespace_id: v06NamespaceId, role: "developer" }],
      },
    }),
  )
  await page.route("**/api/v1/namespaces/mine", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: v06NamespaceId,
            name: "V0.6 验收空间",
            code: "v06",
            is_active: true,
          },
        ],
        count: 1,
      },
    }),
  )
}
