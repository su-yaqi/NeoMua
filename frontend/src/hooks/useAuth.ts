import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import { LoginService, UsersService } from "@/api/generatedCompat"
import type {
  BodyLoginLoginAccessToken as AccessToken,
  UserPublic,
  UserRegister,
} from "@/client"
import { browserAxios } from "@/lib/browserApi"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

const hasAuthenticatedSession = async () => {
  try {
    await UsersService.readUserMe()
    return true
  } catch {
    return false
  }
}

const authPages = new Set([
  "/login",
  "/signup",
  "/recover-password",
  "/reset-password",
])

const useAuth = () => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const { data: user } = useQuery<UserPublic | null, Error>({
    queryKey: ["currentUser"],
    queryFn: UsersService.readUserMe,
    enabled: !authPages.has(window.location.pathname),
    retry: false,
  })

  const signUpMutation = useMutation({
    mutationFn: (data: UserRegister) =>
      UsersService.registerUser({ requestBody: data }),
    onSuccess: () => {
      navigate({ to: "/login" })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })

  const login = async (data: AccessToken) => {
    await LoginService.loginAccessToken({
      formData: data,
    })
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: () => {
      navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = async () => {
    await browserAxios.post("/api/v1/login/logout")
    queryClient.clear()
    navigate({ to: "/login" })
  }

  return {
    signUpMutation,
    loginMutation,
    logout,
    user,
    isLoading: user === undefined,
  }
}

export default useAuth
export { hasAuthenticatedSession }
