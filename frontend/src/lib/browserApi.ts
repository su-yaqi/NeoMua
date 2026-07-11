import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios"

const baseURL = import.meta.env.VITE_API_URL

function cookie(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`
  const value = document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix))
  return value ? decodeURIComponent(value.slice(prefix.length)) : ""
}

export const browserAxios = axios.create({ baseURL, withCredentials: true })
let refreshPromise: Promise<void> | null = null

browserAxios.interceptors.request.use((config) => {
  config.withCredentials = true
  if (["post", "put", "patch", "delete"].includes(config.method ?? "")) {
    config.headers.set("X-CSRF-Token", cookie("neomua_csrf"))
  }
  return config
})

browserAxios.interceptors.response.use(undefined, async (error: AxiosError) => {
  const request = error.config as
    | (InternalAxiosRequestConfig & {
        _authRetried?: boolean
      })
    | undefined
  const isRefresh = request?.url?.replace(/\/+$/, "").endsWith("/login/refresh")
  if (
    error.response?.status !== 401 ||
    !request ||
    request._authRetried ||
    isRefresh
  ) {
    throw error
  }
  request._authRetried = true
  refreshPromise ??= browserAxios
    .post("/api/v1/login/refresh")
    .then(() => undefined)
    .finally(() => {
      refreshPromise = null
    })
  await refreshPromise
  return browserAxios.request(request)
})
