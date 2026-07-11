import { test as setup } from "@playwright/test"
import { randomEmail, randomPassword } from "./utils/random"

const authFile = "playwright/.auth/user.json"

setup("authenticate", async ({ page, request }) => {
  const email = randomEmail()
  const password = randomPassword()

  const createResponse = await request.post(
    "http://127.0.0.1:8000/api/v1/private/users/",
    {
      data: {
        email,
        password,
        full_name: "Playwright Admin",
        is_verified: true,
        is_superuser: true,
        is_active: true,
      },
    },
  )

  if (!createResponse.ok()) {
    throw new Error(
      `Failed to create Playwright superuser: ${createResponse.status()} ${await createResponse.text()}`,
    )
  }

  await page.goto("/login")
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
  await page.getByRole("button", { name: "Log In" }).click()
  await page.waitForURL("/")
  await page.context().storageState({ path: authFile })
})
