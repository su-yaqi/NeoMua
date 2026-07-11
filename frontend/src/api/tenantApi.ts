import { client } from "@/client/client.gen"
import { browserAxios } from "@/lib/browserApi"

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

export type RuntimeRouteMode = "platform_gateway" | "direct_anthropic"

export interface PlatformRuntime {
  id: string
  namespace_id: string
  route_mode: RuntimeRouteMode
  model_id: string
  provider_config_id: string | null
  base_url: string | null
  permission_mode: string
  secret_masked: string | null
  compatibility_verified: boolean
}

export interface RuntimeNode {
  id: string
  name: string
  hostname: string
  os_name: string
  architecture: string
  agent_version: string
  sdk_version: string | null
  online: boolean
  last_seen_at: string | null
  revoked_at: string | null
  runtime_profile_id: string | null
}

export interface RuntimeEvent {
  sequence: number
  event_type: string
  payload: Record<string, unknown>
}

export interface RuntimeTask {
  id: string
  runtime_profile_id: string
  node_id: string | null
  task_kind: "ordinary" | "admin"
  status: string
  prompt: string
  snapshot: Record<string, unknown>
  final_result: Record<string, unknown> | null
  retry_of_task_id: string | null
  created_at: string
  updated_at: string
}

export interface RuntimeArtifact {
  id: string
  kind: string
  logical_target: string
  version: string
  content_sha256: string
  size: number
  manifest: Record<string, unknown>
  signature: string
  signing_public_key: string
}

export interface ArtifactDeployment {
  id: string
  node_id: string
  artifact_id: string
  previous_artifact_id: string | null
  attempt: number
  status: string
  error: Record<string, unknown> | null
}

export interface ArtifactRelease {
  id: string
  artifact_id: string
  valid_until: string
  rollback_of_release_id: string | null
  deployments: ArtifactDeployment[]
}

const api = browserAxios

api.interceptors.request.use((config) => {
  config.baseURL = client.getConfig().baseURL
  config.headers = config.headers || {}
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
    const { data } = await api.get<{
      data: LlmProviderConfig[]
      count: number
    }>("/api/v1/llm/provider-configs")
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
  readPlatformRuntime: async () => {
    const { data } = await api.get<PlatformRuntime>("/api/v1/runtimes/platform")
    return data
  },
  upsertPlatformRuntime: async (body: Record<string, unknown>) => {
    const { data } = await api.put<PlatformRuntime>(
      "/api/v1/runtimes/platform",
      body,
    )
    return data
  },
  validatePlatformRuntime: async () => {
    const { data } = await api.post<PlatformRuntime>(
      "/api/v1/runtimes/platform/validate",
    )
    return data
  },
  createRuntimeSession: async () => {
    const { data } = await api.post<{ id: string }>(
      "/api/v1/runtimes/platform/sessions",
      {},
    )
    return data
  },
  sendRuntimeMessage: async (sessionId: string, prompt: string) => {
    const { data } = await api.post<{ id: string; status: string }>(
      `/api/v1/runtimes/sessions/${sessionId}/messages`,
      { prompt },
    )
    return data
  },
  readRuntimeEvents: async (taskId: string, afterSequence = -1) => {
    const { data } = await api.get<RuntimeEvent[]>(
      `/api/v1/runtimes/tasks/${taskId}/events`,
      { params: { after_sequence: afterSequence } },
    )
    return data
  },
  streamRuntimeEvents: async (
    taskId: string,
    onEvent: (event: RuntimeEvent) => void,
    signal?: AbortSignal,
  ) => {
    const namespaceId = localStorage.getItem("selected_namespace_id") || ""
    const response = await fetch(
      `${client.getConfig().baseURL}/api/v1/runtimes/tasks/${taskId}/stream`,
      {
        headers: {
          "X-Namespace-Id": namespaceId,
        },
        credentials: "include",
        signal,
      },
    )
    if (!response.ok || !response.body) {
      throw new Error(`事件流连接失败 (${response.status})`)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const frames = buffer.split("\n\n")
      buffer = frames.pop() ?? ""
      for (const frame of frames) {
        const data = frame.split("\n").find((line) => line.startsWith("data: "))
        if (data) onEvent(JSON.parse(data.slice(6)) as RuntimeEvent)
      }
      if (done) break
    }
  },
  readRuntimeNodes: async () => {
    const { data } = await api.get<{ data: RuntimeNode[]; count: number }>(
      "/api/v1/runtimes/nodes",
    )
    return data
  },
  readRuntimeNode: async (nodeId: string) => {
    const { data } = await api.get<RuntimeNode>(
      `/api/v1/runtimes/nodes/${nodeId}`,
    )
    return data
  },
  createNodeEnrollmentToken: async () => {
    const { data } = await api.post<{
      id: string
      token: string
      expires_at: string
    }>("/api/v1/runtimes/nodes/enrollment-tokens")
    return data
  },
  configureNodeRuntime: async (
    nodeId: string,
    body: Record<string, unknown>,
  ) => {
    const { data } = await api.put(
      `/api/v1/runtimes/nodes/${nodeId}/runtime`,
      body,
    )
    return data
  },
  revokeNodeCredential: async (nodeId: string) => {
    await api.delete(`/api/v1/runtimes/nodes/${nodeId}/credential`)
  },
  createRuntimeTask: async (
    body: Record<string, unknown>,
    idempotencyKey: string,
  ) => {
    const { data } = await api.post<RuntimeTask>(
      "/api/v1/runtime-tasks",
      body,
      {
        headers: { "Idempotency-Key": idempotencyKey },
      },
    )
    return data
  },
  readRuntimeTasks: async () => {
    const { data } = await api.get<{ data: RuntimeTask[]; count: number }>(
      "/api/v1/runtime-tasks",
    )
    return data
  },
  readRuntimeTask: async (taskId: string) => {
    const { data } = await api.get<RuntimeTask>(
      `/api/v1/runtime-tasks/${taskId}`,
    )
    return data
  },
  cancelRuntimeTask: async (taskId: string) => {
    const { data } = await api.post<RuntimeTask>(
      `/api/v1/runtime-tasks/${taskId}/cancel`,
    )
    return data
  },
  retryRuntimeTask: async (taskId: string, idempotencyKey: string) => {
    const { data } = await api.post<RuntimeTask>(
      `/api/v1/runtime-tasks/${taskId}/retry`,
      {},
      { headers: { "Idempotency-Key": idempotencyKey } },
    )
    return data
  },
  readRuntimeArtifacts: async () => {
    const { data } = await api.get<{ data: RuntimeArtifact[]; count: number }>(
      "/api/v1/runtime-artifacts",
    )
    return data
  },
  uploadRuntimeArtifact: async (form: FormData) => {
    const { data } = await api.post<RuntimeArtifact>(
      "/api/v1/runtime-artifacts",
      form,
    )
    return data
  },
  createArtifactRelease: async (body: Record<string, unknown>) => {
    const { data } = await api.post<ArtifactRelease>(
      "/api/v1/runtime-artifacts/releases",
      body,
    )
    return data
  },
  readArtifactRelease: async (releaseId: string) => {
    const { data } = await api.get<ArtifactRelease>(
      `/api/v1/runtime-artifacts/releases/${releaseId}`,
    )
    return data
  },
  retryArtifactDeployment: async (deploymentId: string) => {
    const { data } = await api.post<ArtifactDeployment>(
      `/api/v1/runtime-artifacts/deployments/${deploymentId}/retry`,
    )
    return data
  },
  rollbackArtifactDeployment: async (deploymentId: string) => {
    const { data } = await api.post<ArtifactRelease>(
      `/api/v1/runtime-artifacts/deployments/${deploymentId}/rollback`,
    )
    return data
  },
}
