import { test as setup } from "@playwright/test"
import { randomEmail, randomPassword } from "./utils/random"

const authFile = "playwright/.auth/user.json"

setup("authenticate", async ({ page, request }) => {
  const email = randomEmail()
  const password = randomPassword()

  const createResponse = await request.post("http://127.0.0.1:8000/api/v1/private/users/", {
    data: {
      email,
      password,
      full_name: "Playwright Admin",
      is_verified: true,
      is_superuser: true,
      is_active: true,
    },
  })

  if (!createResponse.ok()) {
    throw new Error(
      `Failed to create Playwright superuser: ${createResponse.status()} ${await createResponse.text()}`,
    )
  }

  const response = await request.post("http://127.0.0.1:8000/api/v1/login/access-token", {
    form: {
      username: email,
      password,
    },
  })
  const responseText = await response.text()

  if (!response.ok()) {
    throw new Error(`Authentication failed: ${response.status()} ${responseText}`)
  }

  const body = JSON.parse(responseText) as { access_token?: string }
  if (!body.access_token) {
    throw new Error(`Authentication response missing access_token: ${responseText}`)
  }

  await page.goto("/login")
  await page.evaluate((token) => {
    localStorage.setItem("access_token", token)
  }, body.access_token)
  await page.goto("/")
  await page.waitForURL("/")
  await page.context().storageState({ path: authFile })
})
