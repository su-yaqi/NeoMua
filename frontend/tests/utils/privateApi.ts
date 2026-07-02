import { privateCreateUser } from "../../src/client"
import { client } from "../../src/client/client.gen"

client.setConfig({ baseURL: `${process.env.VITE_API_URL}`, throwOnError: true })

export const createUser = async ({
  email,
  password,
  isSuperuser = false,
}: {
  email: string
  password: string
  isSuperuser?: boolean
}) => {
  return (
    await privateCreateUser(
      {
        privateUserCreate: {
          email,
          password,
          is_verified: true,
          full_name: "Test User",
          is_superuser: isSuperuser,
          is_active: true,
        },
      },
      { throwOnError: true },
    )
  ).data
}
