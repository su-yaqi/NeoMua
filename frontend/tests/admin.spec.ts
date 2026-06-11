import { expect, test } from "@playwright/test"

import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

test.describe.configure({ mode: "serial" })

const namespaceRow = (page: import("@playwright/test").Page, namespaceName: string) =>
  page.locator("tr").filter({
    has: page.getByRole("cell", { name: namespaceName, exact: true }),
  })

test("Superuser can access platform user governance", async ({ page }) => {
  await page.goto("/admin")

  await expect(
    page.getByRole("heading", { name: "平台用户治理" }),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "新增平台用户" })).toBeVisible()
})

test("Superuser can create a namespace and open member management", async ({
  page,
}) => {
  const namespaceName = `空间-${Date.now()}`

  await page.goto("/system/namespaces")
  await expect(page.getByRole("heading", { name: "空间治理" })).toBeVisible()

  await page.getByRole("button", { name: "新增空间" }).click()
  await page.getByPlaceholder("请输入空间名称").fill(namespaceName)
  await page.getByRole("button", { name: "保存" }).click()

  await expect(page.getByText("空间创建成功")).toBeVisible()

  const row = namespaceRow(page, namespaceName)
  await expect(row).toBeVisible()

  const membersHref = await row
    .getByRole("link", { name: "成员管理" })
    .getAttribute("href")
  await page.goto(membersHref ?? "/system/namespaces")

  await expect(
    page.getByRole("heading", { name: "空间成员管理" }),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "新增成员" })).toBeVisible()
})

test("Superuser can add a namespace member from the members page", async ({
  page,
}) => {
  const namespaceName = `成员空间-${Date.now()}`
  const email = randomEmail()
  const password = randomPassword()

  await page.goto("/system/namespaces")
  await page.getByRole("button", { name: "新增空间" }).click()
  await page.getByPlaceholder("请输入空间名称").fill(namespaceName)
  await page.getByRole("button", { name: "保存" }).click()
  await expect(page.getByText("空间创建成功")).toBeVisible()

  const row = namespaceRow(page, namespaceName)
  const membersHref = await row
    .getByRole("link", { name: "成员管理" })
    .getAttribute("href")
  await page.goto(membersHref ?? "/system/namespaces")

  await expect(
    page.getByRole("heading", { name: "空间成员管理" }),
  ).toBeVisible()

  await page.getByRole("button", { name: "新增成员" }).click()
  await page.getByPlaceholder("请输入邮箱").fill(email)
  await page.getByPlaceholder("请输入姓名").fill("空间成员")
  await page.getByPlaceholder("仅新用户会使用此密码").fill(password)
  await page.getByRole("button", { name: "保存" }).click()

  await expect(page.getByText("空间成员添加成功")).toBeVisible()
  await expect(page.getByRole("row").filter({ hasText: email })).toBeVisible()
})

test("Superuser can create a platform user with namespace assignment", async ({
  page,
}) => {
  const namespaceName = `平台空间-${Date.now()}`
  const email = randomEmail()
  const password = randomPassword()

  await page.goto("/system/namespaces")
  await page.getByRole("button", { name: "新增空间" }).click()
  await page.getByPlaceholder("请输入空间名称").fill(namespaceName)
  await page.getByRole("button", { name: "保存" }).click()
  await expect(page.getByText("空间创建成功")).toBeVisible()

  await page.goto("/admin")
  await page.getByRole("button", { name: "新增平台用户" }).click()
  await page.getByPlaceholder("请输入邮箱").fill(email)
  await page.getByPlaceholder("请输入姓名").fill("平台成员")
  await page.getByPlaceholder("请输入密码").fill(password)
  await page.getByPlaceholder("请再次输入密码").fill(password)
  const namespaceCheckbox = page.getByRole("checkbox", { name: namespaceName })
  await namespaceCheckbox.scrollIntoViewIfNeeded()
  await namespaceCheckbox.check({ force: true })
  await page.getByRole("button", { name: "保存" }).click()

  const row = page.getByRole("row").filter({ hasText: email })
  await expect(row).toBeVisible()
})

test.describe("Admin page access control", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Non-superuser cannot access admin page", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/admin")

    await expect(
      page.getByRole("heading", { name: "平台用户治理" }),
    ).not.toBeVisible()
    await expect(page).not.toHaveURL(/\/admin/)
  })
})
