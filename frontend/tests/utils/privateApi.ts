// Note: the `PrivateService` is only available when generating the client
// for local environments
import { OpenAPI, PrivateService } from "../../src/client"

OpenAPI.BASE = `${process.env.VITE_API_URL}`

export const createUser = async ({
  email,
  password,
  isSuperuser = false,
}: {
  email: string
  password: string
  isSuperuser?: boolean
}) => {
  return await PrivateService.createUser({
    requestBody: {
      email,
      password,
      is_verified: true,
      full_name: "Test User",
      is_superuser: isSuperuser,
      is_active: true,
    },
  })
}
