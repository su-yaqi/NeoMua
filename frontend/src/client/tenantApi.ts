import axios from "axios"

import { OpenAPI } from "@/client"

export type NamespaceRole = "admin" | "developer" | "user"

export interface NamespacePublic {
  id: string
  name: string
  code: string
  is_active: boolean
}

export type ProviderAuthType =
  | "api_key"
  | "oauth_external"
  | "oauth_device_code"
  | "aws_sdk"
  | "external_process"
  | "copilot_token"
  | "custom"

export type ProviderValidationStatus =
  | "unverified"
  | "success"
  | "failed"
  | "unsupported"

export type ProviderModelSourceType = "discovered" | "manual"
export type ProviderModelSyncStatus = "active" | "stale" | "sync_failed"

export interface LlmProviderCatalogField {
  name: string
  label: string
  required: boolean
  placeholder?: string | null
  help_text?: string | null
}

export interface LlmProviderCatalogItem {
  provider_slug: string
  display_name: string
  description?: string | null
  auth_type: ProviderAuthType
  default_base_url?: string | null
  supports_health_check: boolean
  supports_model_discovery: boolean
  base_url_editable: boolean
  secret_fields: LlmProviderCatalogField[]
  extra_fields: LlmProviderCatalogField[]
}

export interface LlmProviderModel {
  id: string
  model_id: string
  display_name: string | null
  source_type: ProviderModelSourceType
  is_enabled: boolean
  sync_status: ProviderModelSyncStatus
  raw_metadata: Record<string, unknown>
  last_synced_at: string | null
}

export interface LlmProviderConfig {
  id: string
  namespace_id: string
  config_name: string
  provider_slug: string
  provider_display_name: string
  auth_type: ProviderAuthType
  base_url: string
  secret_masked: string | null
  extra_config: Record<string, string>
  supports_health_check: boolean
  supports_model_discovery: boolean
  validation_status: ProviderValidationStatus
  validation_message: string | null
  last_validated_at: string | null
  enabled: boolean
  created_at: string | null
  updated_at: string | null
  models: LlmProviderModel[]
}

export interface LlmProviderModelInput {
  model_id: string
  display_name?: string | null
}

export interface LlmProviderConfigCreateBody {
  config_name: string
  provider_slug: string
  base_url: string
  enabled: boolean
  secret_inputs: Record<string, string>
  extra_config: Record<string, string>
  manual_models: LlmProviderModelInput[]
  enabled_model_ids: string[]
}

export interface LlmProviderConfigUpdateBody {
  config_name?: string
  base_url?: string
  enabled?: boolean
  secret_inputs?: Record<string, string>
  extra_config?: Record<string, string>
  manual_models?: LlmProviderModelInput[]
  enabled_model_ids?: string[]
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
  readLlmProviderCatalog: async () => {
    const { data } = await api.get<{
      data: LlmProviderCatalogItem[]
      count: number
    }>("/api/v1/llm/providers/catalog")
    return data
  },
  readLlmProviderConfigs: async () => {
    const { data } = await api.get<{ data: LlmProviderConfig[]; count: number }>(
      "/api/v1/llm/provider-configs",
    )
    return data
  },
  createLlmProviderConfig: async (body: LlmProviderConfigCreateBody) => {
    const { data } = await api.post<LlmProviderConfig>(
      "/api/v1/llm/provider-configs",
      body,
    )
    return data
  },
  updateLlmProviderConfig: async (
    configId: string,
    body: LlmProviderConfigUpdateBody,
  ) => {
    const { data } = await api.patch<LlmProviderConfig>(
      `/api/v1/llm/provider-configs/${configId}`,
      body,
    )
    return data
  },
  validateLlmProviderConfig: async (configId: string) => {
    const { data } = await api.post<LlmProviderConfig>(
      `/api/v1/llm/provider-configs/${configId}/validate`,
    )
    return data
  },
  syncLlmProviderConfigModels: async (
    configId: string,
    body: {
      manual_models: LlmProviderModelInput[]
      enabled_model_ids: string[]
    },
  ) => {
    const { data } = await api.post<LlmProviderConfig>(
      `/api/v1/llm/provider-configs/${configId}/sync-models`,
      body,
    )
    return data
  },
}
