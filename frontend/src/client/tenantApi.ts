import axios from "axios"

import { OpenAPI } from "@/client"

export type NamespaceRole = "admin" | "developer" | "user"

export interface NamespacePublic {
  id: string
  name: string
  code: string
  is_active: boolean
}

export interface UserNamespaceAssignment {
  namespace_id: string
  role: NamespaceRole
}

export interface PlatformUserUpdateBody {
  email?: string
  full_name?: string
  password?: string
  is_active?: boolean
  is_superuser?: boolean
  namespace_assignments?: UserNamespaceAssignment[]
}

export interface TenantUser {
  id: string
  email: string
  full_name: string | null
  is_active: boolean
  is_superuser: boolean
  namespace_roles: UserNamespaceAssignment[]
}

export interface PlatformUserCreateBody {
  email: string
  full_name?: string
  password: string
  is_active: boolean
  is_superuser: boolean
  namespace_assignments?: UserNamespaceAssignment[]
}

const api = axios.create()

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token") || ""
  config.baseURL = OpenAPI.BASE
  config.headers = config.headers || {}
  config.headers.Authorization = `Bearer ${token}`
  const selectedNamespaceId = localStorage.getItem("selected_namespace_id")
  if (selectedNamespaceId) {
    config.headers["X-Namespace-Id"] = selectedNamespaceId
  }
  return config
})

export const tenantApi = {
  readMyNamespaces: async () => {
    const { data } = await api.get<{ data: NamespacePublic[]; count: number }>(
      "/api/v1/namespaces/mine",
    )
    return data
  },
  readNamespaceUsers: async (namespaceId: string) => {
    const { data } = await api.get<{ data: TenantUser[]; count: number }>(
      `/api/v1/namespaces/${namespaceId}/users`,
      { params: { namespace_id: namespaceId } },
    )
    return data
  },
  createNamespaceUser: async (
    namespaceId: string,
    body: Record<string, unknown>,
  ) => {
    const { data } = await api.post<TenantUser>(
      `/api/v1/namespaces/${namespaceId}/users`,
      body,
    )
    return data
  },
  updateNamespaceUser: async (
    namespaceId: string,
    userId: string,
    body: Record<string, unknown>,
  ) => {
    const { data } = await api.patch<TenantUser>(
      `/api/v1/namespaces/${namespaceId}/users/${userId}`,
      body,
    )
    return data
  },
  deleteNamespaceUser: async (namespaceId: string, userId: string) => {
    const { data } = await api.delete(
      `/api/v1/namespaces/${namespaceId}/users/${userId}`,
    )
    return data
  },
  readSystemNamespaces: async () => {
    const { data } = await api.get<{ data: NamespacePublic[]; count: number }>(
      "/api/v1/platform/namespaces",
    )
    return data
  },
  createSystemNamespace: async (body: Record<string, unknown>) => {
    const { data } = await api.post<NamespacePublic>(
      "/api/v1/platform/namespaces",
      body,
    )
    return data
  },
  updateSystemNamespace: async (
    namespaceId: string,
    body: Record<string, unknown>,
  ) => {
    const { data } = await api.patch<NamespacePublic>(
      `/api/v1/platform/namespaces/${namespaceId}`,
      body,
    )
    return data
  },
  deleteSystemNamespace: async (namespaceId: string) => {
    const { data } = await api.delete(
      `/api/v1/platform/namespaces/${namespaceId}`,
    )
    return data
  },
  readPlatformUsers: async () => {
    const { data } = await api.get<{ data: TenantUser[]; count: number }>(
      "/api/v1/platform/users",
    )
    return data
  },
  createPlatformUser: async (body: PlatformUserCreateBody) => {
    const { data } = await api.post<TenantUser>("/api/v1/platform/users", body)
    return data
  },
  updatePlatformUser: async (userId: string, body: PlatformUserUpdateBody) => {
    const { data } = await api.patch<TenantUser>(
      `/api/v1/platform/users/${userId}`,
      body,
    )
    return data
  },
  deletePlatformUser: async (userId: string) => {
    const { data } = await api.delete(`/api/v1/platform/users/${userId}`)
    return data
  },
}
