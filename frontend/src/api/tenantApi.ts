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

export type LlmProviderModelPreview = Omit<LlmProviderModel, "id">

export interface LlmProviderDraftResult {
  validation_status: ProviderValidationStatus
  validation_message: string
  models: LlmProviderModelPreview[]
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
  validate_on_create?: boolean
  sync_models_on_create?: boolean
}

export interface LlmProviderDraftBody {
  provider_slug: string
  base_url: string
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

export interface HarnessCapability {
  cli_version: string
  sdk_version?: string
  harness_version?: string
  adapter_version?: string
  builtin_tools?: string[]
  supports_tool_filters?: boolean
  supports_per_tool_approval?: boolean
  supports_mcp_injection?: boolean
  permission_modes?: string[]
  executable?: string
}

export interface HarnessCapabilities {
  claude_agent_sdk?: HarnessCapability
  claude_code?: HarnessCapability
  codex?: HarnessCapability
  mcp_executables?: string[]
}

export interface RuntimeNode {
  id: string
  name: string
  hostname: string
  os_name: string
  architecture: string
  agent_version: string
  sdk_version: string | null
  harness_capabilities: HarnessCapabilities
  online: boolean
  last_seen_at: string | null
  revoked_at: string | null
  runtime_profile_id: string | null
  management_mode: "service" | "client" | "legacy_unclassified"
  adapter_registry_digest: string | null
  current_installation_receipt_id: string | null
  installation_manifest_digest: string | null
  bootstrap_session_id: string | null
  bootstrap_status:
    | "waiting_for_install"
    | "preflighted"
    | "staged"
    | "enrolled"
    | "service_activated"
    | "reconciled"
    | "failed"
    | "revoked"
    | null
  discovery_generation: number
  discovery_requested_generation: number
}

export interface NodeBootstrapSession {
  id: string
  management_mode: "service" | "client"
  release_channel: string
  distribution_release_id: string | null
  status: NonNullable<RuntimeNode["bootstrap_status"]>
  node_id: string | null
  expires_at: string
  completed_at: string | null
}

export interface RuntimeDiscoveryObservation {
  id: string
  node_id: string
  runtime_instance_id: string | null
  adapter_release_id: string | null
  generation: number
  installation_key: string
  status: "available" | "missing" | "blocked"
  evidence: Record<string, unknown>
  evidence_digest: string
  observed_at: string
}

export interface ProviderRuntimeReadiness {
  status: "reconciling" | "not_configured" | "ready" | "blocked"
  expected_worker_release_digest: string | null
  reported_worker_release_digest: string | null
  release_trusted: boolean
  runtime_instance_id: string | null
  runtime_status: string | null
  reconcile_job_id: string | null
  reconcile_status: string | null
  reconcile_trigger: string | null
  reconcile_attempt_count: number
  reconcile_error: Record<string, unknown> | null
  models: Array<{
    provider_model_id: string
    model_id: string
    enabled: boolean
    model_definition_id: string | null
    validation_status: string
    validation_error: Record<string, unknown> | null
    validation_completed_at: string | null
    validation_valid_until: string | null
    binding_id: string | null
    binding_status: string | null
    binding_error: Record<string, unknown> | null
    ready: boolean
  }>
}

export interface RuntimeInstanceSummary {
  id: string
  namespace_id: string
  runtime_node_id: string | null
  location_type: "platform" | "node"
  management_type:
    | "platform_builtin"
    | "service_managed"
    | "client_discovered"
    | "legacy_manual"
  lifecycle_source_key: string | null
  name: string
  installation_key: string
  engine_type: "claude_agent_sdk" | "claude_code" | "codex"
  engine_version: string | null
  adapter_version: string
  status:
    | "discovered"
    | "available"
    | "unavailable"
    | "incompatible"
    | "disabled"
  evidence_state: string
  enabled: boolean
  available_model_count: number
  applied_configuration_revision_id: string | null
  current_capability_report_id: string | null
  last_seen_at: string | null
}

export interface ModelDefinition {
  id: string
  namespace_id: string
  provider_family: string
  model_key: string
  display_name: string | null
  capability_tags: string[]
  enabled: boolean
}

export interface RuntimeModelBinding {
  id: string
  runtime_instance_id: string
  model_definition_id: string
  provider_config_id: string | null
  provider_model_id: string | null
  route_type: "provider_config" | "runtime_native" | "legacy_direct"
  route_key: string
  engine_model_id: string
  status: "declared" | "available" | "unmapped" | "failed" | "disabled"
  validation_fingerprint: string | null
  last_validated_at: string | null
  last_error: Record<string, unknown> | null
}

export interface RuntimeConfigurationRevision {
  id: string
  revision: number
  origin:
    | "system_builtin"
    | "service_manifest"
    | "client_adapter"
    | "legacy_manual"
  adapter_execution_ref: string | null
  executable: string
  arguments: string[]
  working_directory_policy: string
  environment_allowlist: string[]
  security_policy: Record<string, unknown>
  resource_limits: Record<string, unknown>
  configuration_digest: string
  status: "desired" | "applying" | "applied" | "failed"
  error: Record<string, unknown> | null
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
  skill_usage: Array<Record<string, unknown>>
  model_usage: Record<string, unknown> | null
  model_call_usage: Array<Record<string, unknown>>
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
  readLlmProviderRuntimeReadiness: async (configId: string) =>
    (
      await api.get<ProviderRuntimeReadiness>(
        `/api/v1/llm/provider-configs/${configId}/runtime-readiness`,
      )
    ).data,
  validateLlmProviderDraft: async (body: LlmProviderDraftBody) => {
    const { data } = await api.post<LlmProviderDraftResult>(
      "/api/v1/llm/provider-configs/draft/validate",
      body,
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
  syncLlmProviderDraftModels: async (body: LlmProviderDraftBody) => {
    const { data } = await api.post<LlmProviderDraftResult>(
      "/api/v1/llm/provider-configs/draft/sync-models",
      body,
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
  createNodeBootstrapSession: async (managementMode: "service" | "client") => {
    const { data } = await api.post<{
      id: string
      token: string
      expires_at: string
      management_mode: "service" | "client"
      bootstrap_session_id: string
      release_channel: string
      status: NonNullable<RuntimeNode["bootstrap_status"]>
      signing_public_key: string
    }>("/api/v1/runtimes/nodes/bootstrap-sessions", {
      management_mode: managementMode,
    })
    return data
  },
  readNodeBootstrapSessions: async () =>
    (
      await api.get<NodeBootstrapSession[]>(
        "/api/v1/runtimes/nodes/bootstrap-sessions",
      )
    ).data,
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
  readRuntimeSkills: async (runtimeId: string) =>
    (
      await api.get<{ data: Record<string, unknown>[]; count: number }>(
        `/api/v1/runtimes/${runtimeId}/skills`,
      )
    ).data,
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
  readTaskApprovals: async (taskId: string) =>
    (await api.get(`/api/v1/runtime-tasks/${taskId}/approvals`)).data,
  decideToolApproval: async (
    approvalId: string,
    decision: "approve" | "deny",
    argsDigest: string,
  ) =>
    (
      await api.post(`/api/v1/tool-approvals/${approvalId}/${decision}`, {
        args_digest: argsDigest,
      })
    ).data,
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

export const runtimeInstancesApi = {
  list: async () =>
    (
      await api.get<{ data: RuntimeInstanceSummary[]; count: number }>(
        "/api/v1/runtimes",
      )
    ).data,
  get: async (runtimeId: string) =>
    (
      await api.get<
        RuntimeInstanceSummary & {
          configurations: RuntimeConfigurationRevision[]
          capability_reports: Array<Record<string, unknown>>
        }
      >(`/api/v1/runtimes/${runtimeId}`)
    ).data,
  listNode: async (nodeId: string) =>
    (
      await api.get<{ data: RuntimeInstanceSummary[]; count: number }>(
        `/api/v1/runtime-nodes/${nodeId}/runtimes`,
      )
    ).data,
  listDiscoveryObservations: async (nodeId: string) =>
    (
      await api.get<{
        data: RuntimeDiscoveryObservation[]
        count: number
      }>(`/api/v1/runtime-nodes/${nodeId}/discovery-observations`)
    ).data,
  requestDiscoveryRefresh: async (nodeId: string) =>
    (
      await api.post<{ status: string; generation: number }>(
        `/api/v1/runtime-nodes/${nodeId}/discovery/refresh`,
      )
    ).data,
  pause: async (runtimeId: string) =>
    (
      await api.post<RuntimeInstanceSummary>(
        `/api/v1/runtimes/${runtimeId}/pause`,
      )
    ).data,
  resume: async (runtimeId: string) =>
    (
      await api.post<RuntimeInstanceSummary>(
        `/api/v1/runtimes/${runtimeId}/resume`,
      )
    ).data,
  enableNode: async (nodeId: string, runtimeId: string) =>
    (
      await api.post<RuntimeInstanceSummary>(
        `/api/v1/runtime-nodes/${nodeId}/runtimes/${runtimeId}/enable`,
      )
    ).data,
  saveConfiguration: async (runtimeId: string, body: Record<string, unknown>) =>
    (await api.put(`/api/v1/runtimes/${runtimeId}/configuration`, body)).data,
  applyConfiguration: async (runtimeId: string, revisionId: string) =>
    (
      await api.post(`/api/v1/runtimes/${runtimeId}/configuration/apply`, {
        configuration_revision_id: revisionId,
      })
    ).data,
  listBindings: async (runtimeId: string) =>
    (
      await api.get<{ data: RuntimeModelBinding[]; count: number }>(
        `/api/v1/runtimes/${runtimeId}/model-bindings`,
      )
    ).data,
  createBinding: async (runtimeId: string, body: Record<string, unknown>) =>
    (
      await api.post<RuntimeModelBinding>(
        `/api/v1/runtimes/${runtimeId}/model-bindings`,
        body,
      )
    ).data,
  validateBinding: async (runtimeId: string, bindingId: string) =>
    (
      await api.post(
        `/api/v1/runtimes/${runtimeId}/model-bindings/${bindingId}/validate`,
        {},
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
  disableBinding: async (runtimeId: string, bindingId: string) =>
    (
      await api.post(
        `/api/v1/runtimes/${runtimeId}/model-bindings/${bindingId}/disable`,
      )
    ).data,
}

export const modelDefinitionsApi = {
  list: async () =>
    (
      await api.get<{ data: ModelDefinition[]; count: number }>(
        "/api/v1/llm/model-definitions",
      )
    ).data,
  create: async (body: {
    provider_family: string
    model_key: string
    display_name?: string
    capability_tags?: string[]
  }) =>
    (await api.post<ModelDefinition>("/api/v1/llm/model-definitions", body))
      .data,
}

export interface AgentDefinition {
  id: string
  namespace_id: string
  slug: string
  name: string
  description: string | null
  status: "active" | "archived"
  created_at: string
  updated_at: string
}

export interface AgentListItem extends AgentDefinition {
  draft_revision: number
  validation_status: "unvalidated" | "validated" | "stale" | "error"
  harness_type: string | null
  model_id: string | null
  preferred_model_definition_id: string | null
}

export interface AgentDraftPublic {
  agent_id: string
  revision: number
  harness_profile_id: string | null
  provider_config_id: string | null
  model_id: string | null
  preferred_model_definition_id: string | null
  execution_policy: Record<string, unknown>
  system_prompt: string
  config: Record<string, unknown>
  validated_revision: number | null
  validation_result: Record<string, unknown> | null
  validation_status: "unvalidated" | "validated" | "stale" | "error"
  updated_at: string
}

export interface HarnessProfilePublic {
  id: string
  namespace_id: string
  name: string
  harness_type: string
  config_schema_version: string
  cli_version_constraint: string
  sdk_version_constraint: string
  config: Record<string, unknown>
  archived: boolean
  referenced_by_agents: boolean
  target_compatibility: TargetCompatibility[]
  created_at: string
  updated_at: string
}

export interface HarnessCatalogField {
  name: string
  type: string
  allowed?: string[]
  max?: number
  allowlist?: string[]
}

export interface HarnessCatalogItem {
  type: string
  config_schema_version: string
  supported: boolean
  description: string
  fields: HarnessCatalogField[]
}

export interface EnvironmentCatalog {
  allowlist: string[]
  reserved: string[]
  denylist: string[]
}

export interface Diagnostic {
  code: string
  field: string
  message: string
}

export interface TargetCompatibility {
  runtime_profile_id: string
  runtime_type: string
  cli_version: string | null
  sdk_version: string | null
  harness_version: string | null
  compatible: boolean | null
  reason: string | null
}

export interface ValidationResult {
  validated_revision: number | null
  status: string
  errors: Diagnostic[]
  warnings: Diagnostic[]
  target_compatibility: TargetCompatibility[]
}

export const agentsApi = {
  list: async () => {
    const { data } = await api.get<{ data: AgentListItem[]; count: number }>(
      "/api/v1/agents",
    )
    return data
  },
  create: async (body: {
    slug: string
    name: string
    description?: string
  }) => {
    const { data } = await api.post<AgentDefinition>("/api/v1/agents", body)
    return data
  },
  createComplete: async (body: {
    slug: string
    name: string
    description?: string
    preferred_model_definition_id: string
    execution_policy?: Record<string, unknown>
    system_prompt: string
    config: Record<string, unknown>
  }) => {
    const { data } = await api.post<{
      agent: AgentDefinition
      draft: AgentDraftPublic
    }>("/api/v1/agents/complete", body)
    return data
  },
  copy: async (agentId: string, body: { slug: string; name: string }) => {
    const { data } = await api.post<AgentDefinition>(
      `/api/v1/agents/${agentId}/copy`,
      body,
    )
    return data
  },
  get: async (agentId: string) => {
    const { data } = await api.get<AgentDefinition>(`/api/v1/agents/${agentId}`)
    return data
  },
  update: async (
    agentId: string,
    body: {
      name?: string
      description?: string | null
      status?: "active" | "archived"
    },
  ) => {
    const { data } = await api.patch<AgentDefinition>(
      `/api/v1/agents/${agentId}`,
      body,
    )
    return data
  },
  delete: async (agentId: string) => {
    await api.delete(`/api/v1/agents/${agentId}`)
  },
  getDraft: async (agentId: string) => {
    const { data } = await api.get<AgentDraftPublic>(
      `/api/v1/agents/${agentId}/draft`,
    )
    return data
  },
  saveDraft: async (
    agentId: string,
    body: {
      expected_revision: number
      preferred_model_definition_id?: string | null
      execution_policy?: Record<string, unknown>
      system_prompt?: string
      config?: Record<string, unknown>
    },
  ) => {
    const { data } = await api.put<AgentDraftPublic>(
      `/api/v1/agents/${agentId}/draft`,
      body,
    )
    return data
  },
  validate: async (agentId: string) => {
    const { data } = await api.post<ValidationResult>(
      `/api/v1/agents/${agentId}/draft/validate`,
    )
    return data
  },
  getCapabilities: async (agentId: string) =>
    (await api.get(`/api/v1/agents/${agentId}/draft/capabilities`)).data,
  setSkills: async (
    agentId: string,
    expectedRevision: number,
    skills: Array<{ skill_id: string; enabled: boolean }>,
  ) =>
    (
      await api.put(`/api/v1/agents/${agentId}/draft/skills`, {
        expected_revision: expectedRevision,
        skills,
      })
    ).data,
  setTools: async (
    agentId: string,
    expectedRevision: number,
    tools: Array<{ tool_key: string; policy: string }>,
  ) =>
    (
      await api.put(`/api/v1/agents/${agentId}/draft/tools`, {
        expected_revision: expectedRevision,
        tools,
      })
    ).data,
  setMcp: async (
    agentId: string,
    expectedRevision: number,
    mcp: Array<{ revision_id: string; allowed_tools: string[] }>,
  ) =>
    (
      await api.put(`/api/v1/agents/${agentId}/draft/mcp`, {
        expected_revision: expectedRevision,
        mcp,
      })
    ).data,
  setPlugins: async (
    agentId: string,
    expectedRevision: number,
    pluginVersionIds: string[],
  ) =>
    (
      await api.put(`/api/v1/agents/${agentId}/draft/plugins`, {
        expected_revision: expectedRevision,
        plugins: pluginVersionIds.map((plugin_version_id) => ({
          plugin_version_id,
        })),
      })
    ).data,
}

export const harnessProfilesApi = {
  list: async () => {
    const { data } = await api.get<{
      data: HarnessProfilePublic[]
      count: number
    }>("/api/v1/harness-profiles")
    return data
  },
  create: async (body: {
    name: string
    harness_type?: string
    config_schema_version?: string
    cli_version_constraint?: string
    sdk_version_constraint?: string
    config?: Record<string, unknown>
  }) => {
    const { data } = await api.post<HarnessProfilePublic>(
      "/api/v1/harness-profiles",
      body,
    )
    return data
  },
  get: async (profileId: string) => {
    const { data } = await api.get<HarnessProfilePublic>(
      `/api/v1/harness-profiles/${profileId}`,
    )
    return data
  },
  update: async (
    profileId: string,
    body: {
      name?: string
      cli_version_constraint?: string
      sdk_version_constraint?: string
      config?: Record<string, unknown>
      archived?: boolean
    },
  ) => {
    const { data } = await api.patch<HarnessProfilePublic>(
      `/api/v1/harness-profiles/${profileId}`,
      body,
    )
    return data
  },
  delete: async (profileId: string) => {
    await api.delete(`/api/v1/harness-profiles/${profileId}`)
  },
}

export interface ManagedIdentity {
  id: string
  namespace_id: string
  slug: string
  name: string
  description: string | null
  archived: boolean
  created_at: string
  updated_at: string
}

export interface SkillIdentity extends ManagedIdentity {
  current_version_id: string
  draft_id: string | null
  current_version: SkillVersion
  version_count: number
  reference_count: number
  file_count: number
  total_size: number
  sync_summary: Record<string, number>
  draft_revision: number | null
  draft_dirty: boolean
}

export interface SkillVersion {
  id: string
  skill_id: string
  version: string
  content_sha256: string
  manifest: Record<string, unknown>
  deprecated: boolean
}

export interface SkillDraftFile {
  id: string
  path: string
  mime_type: string
  size: number
  content_sha256: string
  is_text: boolean
  updated_at: string
}

export interface SkillDraft {
  id: string
  skill_id: string
  revision: number
  base_version_id: string | null
  content_sha256: string | null
  validated_revision: number | null
  validation_result: Record<string, unknown> | null
  dirty: boolean
  files: SkillDraftFile[]
  updated_at: string
}

export interface PluginVersion {
  id: string
  plugin_id: string
  version: string
  manifest_digest: string
  manifest: Record<string, unknown>
  deprecated: boolean
}

export interface AgentRelease {
  id: string
  agent_id: string
  version: string
  draft_revision: number
  resolved_spec_digest: string
  manifest: Record<string, unknown>
  dependency_lock: Record<string, unknown>
  created_at: string
}

const identityApi = (path: string) => ({
  list: async () =>
    (await api.get<{ data: ManagedIdentity[]; count: number }>(path)).data,
  create: async (body: { slug: string; name: string; description?: string }) =>
    (await api.post<ManagedIdentity>(path, body)).data,
  get: async (id: string) =>
    (await api.get<Record<string, unknown>>(`${path}/${id}`)).data,
  update: async (id: string, body: Record<string, unknown>) =>
    (await api.patch<ManagedIdentity>(`${path}/${id}`, body)).data,
})

export const skillsApi = {
  ...identityApi("/api/v1/skills"),
  list: async (params?: {
    q?: string
    archived?: boolean
    draft_dirty?: boolean
  }) =>
    (
      await api.get<{ data: SkillIdentity[]; count: number }>(
        "/api/v1/skills",
        {
          params,
        },
      )
    ).data,
  get: async (skillId: string) =>
    (
      await api.get<
        SkillIdentity & {
          draft: SkillDraft
          versions: SkillVersion[]
        }
      >(`/api/v1/skills/${skillId}`)
    ).data,
  createComplete: async (body: {
    slug: string
    name: string
    description?: string
    version: string
    file: File
  }) => {
    const form = new FormData()
    form.append("slug", body.slug)
    form.append("name", body.name)
    if (body.description) form.append("description", body.description)
    form.append("version", body.version)
    form.append("file", body.file)
    return (
      await api.post<{ skill: ManagedIdentity; version: SkillVersion }>(
        "/api/v1/skills/complete",
        form,
      )
    ).data
  },
  createFromEditor: async (body: {
    slug: string
    name: string
    description?: string
    version: string
    skill_md: string
  }) =>
    (
      await api.post<{ skill: ManagedIdentity; version: SkillVersion }>(
        "/api/v1/skills/complete/editor",
        body,
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
  importDraft: async (
    skillId: string,
    version: string,
    expectedRevision: number,
    file: File,
  ) => {
    const form = new FormData()
    form.append("version", version)
    form.append("expected_revision", String(expectedRevision))
    form.append("file", file)
    return (
      await api.post<SkillDraft>(`/api/v1/skills/${skillId}/draft/import`, form)
    ).data
  },
  versions: async (skillId: string) =>
    (
      await api.get<{ data: SkillVersion[]; count: number }>(
        `/api/v1/skills/${skillId}/versions`,
      )
    ).data,
  versionFiles: async (skillId: string, version: string) =>
    (
      await api.get<{
        data: Array<{ path: string; size: number; sha256: string }>
        count: number
      }>(
        `/api/v1/skills/${skillId}/versions/${encodeURIComponent(version)}/files`,
      )
    ).data,
  versionFile: async (skillId: string, version: string, path: string) =>
    (
      await api.get<{ is_text: boolean; content: string | null }>(
        `/api/v1/skills/${skillId}/versions/${encodeURIComponent(version)}/files/${path
          .split("/")
          .map(encodeURIComponent)
          .join("/")}`,
      )
    ).data,
  draft: async (skillId: string) =>
    (await api.get<SkillDraft>(`/api/v1/skills/${skillId}/draft`)).data,
  draftFile: async (skillId: string, fileId: string) =>
    (
      await api.get<{
        file: SkillDraftFile
        content: string | null
        content_base64: string | null
      }>(`/api/v1/skills/${skillId}/draft/files/${fileId}`)
    ).data,
  saveDraftFile: async (
    skillId: string,
    fileId: string,
    body: { expected_revision: number; content: string },
  ) =>
    (
      await api.put<{ revision: number; file: SkillDraftFile }>(
        `/api/v1/skills/${skillId}/draft/files/${fileId}`,
        body,
      )
    ).data,
  createDraftFile: async (
    skillId: string,
    body: { expected_revision: number; path: string; content: string },
  ) =>
    (
      await api.post<{ revision: number; file: SkillDraftFile }>(
        `/api/v1/skills/${skillId}/draft/files`,
        body,
      )
    ).data,
  moveDraftFile: async (
    skillId: string,
    fileId: string,
    body: { expected_revision: number; path: string },
  ) =>
    (
      await api.post<{ revision: number; file: SkillDraftFile }>(
        `/api/v1/skills/${skillId}/draft/files/${fileId}/move`,
        body,
      )
    ).data,
  deleteDraftFile: async (
    skillId: string,
    fileId: string,
    expectedRevision: number,
  ) =>
    (
      await api.delete<{ revision: number }>(
        `/api/v1/skills/${skillId}/draft/files/${fileId}`,
        { data: { expected_revision: expectedRevision } },
      )
    ).data,
  validateDraft: async (skillId: string, expectedRevision: number) =>
    (
      await api.post(`/api/v1/skills/${skillId}/draft/validate`, {
        expected_revision: expectedRevision,
      })
    ).data,
  publishDraft: async (
    skillId: string,
    body: { expected_revision: number; version: string },
  ) =>
    (
      await api.post<SkillVersion>(
        `/api/v1/skills/${skillId}/draft/publish`,
        body,
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
  setCurrentVersion: async (skillId: string, versionId: string) =>
    (
      await api.post<SkillVersion>(
        `/api/v1/skills/${skillId}/current-version`,
        { version_id: versionId },
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
  runtimeSync: async (skillId: string) =>
    (
      await api.get<{ data: Record<string, unknown>[]; count: number }>(
        `/api/v1/skills/${skillId}/runtime-sync`,
      )
    ).data,
  retryRuntimeSync: async (skillId: string, runtimeId: string) =>
    (
      await api.post(
        `/api/v1/skills/${skillId}/runtime-sync/${runtimeId}/retry`,
      )
    ).data,
  deprecate: async (skillId: string, version: string) =>
    (
      await api.post(
        `/api/v1/skills/${skillId}/versions/${encodeURIComponent(version)}/deprecate`,
      )
    ).data,
}

export const toolsApi = {
  list: async () =>
    (
      await api.get<{ data: Record<string, unknown>[]; count: number }>(
        "/api/v1/tools/catalog",
      )
    ).data,
  setPolicy: async (toolKey: string, policy: string) =>
    (
      await api.put(
        `/api/v1/tools/${encodeURIComponent(toolKey)}/namespace-policy`,
        { tool_key: toolKey, policy },
      )
    ).data,
}

export const mcpServersApi = {
  ...identityApi("/api/v1/mcp-servers"),
  createComplete: async (body: Record<string, unknown>) =>
    (
      await api.post<{
        server: ManagedIdentity
        revision: Record<string, unknown>
        target: { id: string; runtime_instance_id: string; status: string }
      }>("/api/v1/mcp-servers/complete", body)
    ).data,
  revisions: async (serverId: string) =>
    (
      await api.get<{ data: Record<string, unknown>[]; count: number }>(
        `/api/v1/mcp-servers/${serverId}/revisions`,
      )
    ).data,
  createRevision: async (serverId: string, body: Record<string, unknown>) =>
    (await api.post(`/api/v1/mcp-servers/${serverId}/revisions`, body)).data,
  getRevision: async (serverId: string, revision: number) =>
    (await api.get(`/api/v1/mcp-servers/${serverId}/revisions/${revision}`))
      .data,
  createTarget: async (
    revisionId: string,
    body: { runtime_instance_id: string; secret_ref?: string },
  ) =>
    (await api.post(`/api/v1/mcp-revisions/${revisionId}/targets`, body)).data,
  setTargetSecret: async (
    targetId: string,
    secretInputs: Record<string, string>,
  ) =>
    (
      await api.put(`/api/v1/mcp-targets/${targetId}/secret`, {
        secret_inputs: secretInputs,
      })
    ).data,
  validateTarget: async (targetId: string) =>
    (await api.post(`/api/v1/mcp-targets/${targetId}/validate`)).data,
  validations: async (targetId: string) =>
    (await api.get(`/api/v1/mcp-targets/${targetId}/validations`)).data,
  runtime: async (targetId: string) =>
    (await api.get(`/api/v1/mcp-targets/${targetId}/runtime`)).data,
  restartRuntime: async (targetId: string) =>
    (await api.post(`/api/v1/mcp-targets/${targetId}/runtime/restart`)).data,
}

export const pluginsApi = {
  ...identityApi("/api/v1/plugins"),
  createComplete: async (body: Record<string, unknown>) =>
    (
      await api.post<{
        plugin: ManagedIdentity
        draft: Record<string, unknown>
      }>("/api/v1/plugins/complete", body)
    ).data,
  getDraft: async (pluginId: string) =>
    (await api.get(`/api/v1/plugins/${pluginId}/draft`)).data,
  saveDraft: async (pluginId: string, body: Record<string, unknown>) =>
    (await api.put(`/api/v1/plugins/${pluginId}/draft`, body)).data,
  validate: async (pluginId: string) =>
    (await api.post(`/api/v1/plugins/${pluginId}/draft/validate`)).data,
  publish: async (
    pluginId: string,
    body: { draft_revision: number; version: string },
  ) =>
    (
      await api.post<PluginVersion>(
        `/api/v1/plugins/${pluginId}/versions`,
        body,
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
  deprecate: async (pluginId: string, version: string) =>
    (
      await api.post(
        `/api/v1/plugins/${pluginId}/versions/${encodeURIComponent(version)}/deprecate`,
      )
    ).data,
}

export const releasesApi = {
  list: async (agentId: string) =>
    (
      await api.get<{ data: AgentRelease[]; count: number }>(
        `/api/v1/agents/${agentId}/releases`,
      )
    ).data,
  create: async (
    agentId: string,
    body: { draft_revision: number; version: string },
  ) =>
    (
      await api.post<AgentRelease>(`/api/v1/agents/${agentId}/releases`, body, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).data,
  get: async (releaseId: string) =>
    (await api.get(`/api/v1/agent-releases/${releaseId}`)).data,
  activate: async (
    releaseId: string,
    body: {
      runtime_instance_id: string
      precheck_id: string
      precheck_digest: string
    },
  ) =>
    (
      await api.post(`/api/v1/agent-releases/${releaseId}/activations`, body, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).data,
  precheckActivation: async (releaseId: string, runtimeInstanceId: string) =>
    (
      await api.post(
        `/api/v1/agent-releases/${releaseId}/activations/precheck`,
        { runtime_instance_id: runtimeInstanceId },
      )
    ).data,
  runtimeAgents: async () => (await api.get("/api/v1/runtime-agents")).data,
  getActivation: async (activationId: string) =>
    (await api.get(`/api/v1/agent-activations/${activationId}`)).data,
  retryDeployment: async (deploymentId: string) =>
    (await api.post(`/api/v1/agent-deployments/${deploymentId}/retry`)).data,
  rollbackDeployment: async (deploymentId: string) =>
    (
      await api.post(
        `/api/v1/agent-deployments/${deploymentId}/rollback`,
        {},
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
}

export const harnessCatalogApi = {
  harnesses: async () => {
    const { data } = await api.get<{ harnesses: HarnessCatalogItem[] }>(
      "/api/v1/harnesses/catalog",
    )
    return data.harnesses
  },
  environment: async () => {
    const { data } = await api.get<EnvironmentCatalog>(
      "/api/v1/harnesses/environment-catalog",
    )
    return data
  },
}

export type ProjectStatus = "active" | "archived"
export interface ProjectSummary {
  id: string
  namespace_id: string
  slug: string
  name: string
  description: string | null
  status: ProjectStatus
  default_runtime_id: string | null
  default_runtime_instance_id: string | null
  member_ids: string[]
  created_at: string
  updated_at: string
}

export interface ProjectRepository {
  id: string
  project_id: string
  remote_url: string
  purpose: string
  default_branch: string | null
  credential_ref: string | null
  status: "unvalidated" | "available" | "unavailable"
  validated_commit: string | null
  validation_error: Record<string, unknown> | null
}

export interface ProjectSpecLocation {
  id: string
  project_id: string
  repository_id: string
  path: string
  location_type: "directory" | "file"
  description: string
  status: "pending_initialization" | "valid" | "unavailable"
  binding: {
    id: string
    standard_version_id: string
    status: "pending" | "valid" | "conflict"
    validated_commit: string | null
  } | null
}

export interface ConversationRuntime {
  id: string
  runtime_instance_id: string
  runtime_node_id: string | null
  runtime_type: "platform" | "node"
  engine_type: "claude_agent_sdk" | "claude_code" | "codex"
  name: string
  compatible: boolean
}

export interface ConversationModelCatalogItem {
  runtime_model_binding_id: string
  model_definition_id: string
  engine_model_id: string
  route_type: "provider_config" | "runtime_native" | "legacy_direct"
  route_key: string | null
}

export interface ConversationAgentCatalogItem {
  runtime_agent_release_id: string
  agent_id: string
  agent_name: string
  agent_slug: string
  release_id: string
  release_version: string
  resolved_spec_digest: string
  preferred_model_definition_id: string
  preference_binding_ids: string[]
  active: boolean
}

export interface ConversationAgentParticipant {
  id: string
  role: "main" | "collaborator"
  agent_id: string
  agent_release_id: string
  resolved_spec_digest: string
  runtime_agent_release_id: string
  active: boolean
}

export interface ConversationConfigurationRevision {
  id: string
  conversation_id: string
  revision: number
  mode: "chat" | "agent"
  provider_config_id: string | null
  model_id: string | null
  organizer_agent_id: string | null
  participant_ids: string[]
  execution_bindings: Array<{
    id: string
    role_key: string
    runtime_instance_id: string
    runtime_agent_release_id: string | null
    agent_release_id: string | null
    model_selection_mode: "exact" | "agent_preference"
    preferred_model_definition_id: string | null
    runtime_model_binding_id: string
    selection_source: string
    effective_spec_digest: string
  }>
  runtime_model_catalog_fingerprint: string | null
  created_by: string | null
  created_at: string
}

export interface ConversationSummary {
  id: string
  title: string
  mode: "chat" | "agent"
  visibility: "private" | "project"
  status: "active" | "archived"
  runtime_id: string | null
  runtime_instance_id: string | null
  provider_config_id: string | null
  model_id: string | null
  project_id: string | null
  current_context_snapshot_id: string | null
  current_configuration_revision_id: string | null
  configuration: ConversationConfigurationRevision | null
  agents: ConversationAgentParticipant[]
  updated_at: string
}

export interface ConversationMessage {
  id: string
  sequence: number
  author_type: "user" | "model" | "agent" | "system"
  author_id: string | null
  target_type: "main" | "agent" | "all" | "model" | "system"
  target_agent_id: string | null
  configuration_revision_id: string | null
  payload: Record<string, unknown>
  status: "queued" | "running" | "completed" | "failed"
  error: Record<string, unknown> | null
  created_at: string
}

export interface ConversationEvent {
  id: string
  conversation_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface AgentDelegation {
  id: string
  source_message_id: string
  source_agent_id: string
  target_agent_id: string
  status: "queued" | "running" | "completed" | "failed"
  input_payload: Record<string, unknown>
  result_payload: Record<string, unknown> | null
  error: Record<string, unknown> | null
  task_id: string | null
  created_at: string
  completed_at: string | null
}

export interface ConversationAttachment {
  id: string
  filename: string
  content_type: string
  size: number
  content_digest: string
  scan_status: "pending" | "clean" | "rejected"
  scan_details: Record<string, unknown> | null
  created_at: string
}

export interface WorkflowNodeInstance {
  id: string
  node_key: string
  status: string
  expected_revision: number
  assignee_id: string | null
  resolved_runtime_id: string | null
}

export interface WorkflowExecutionConfiguration {
  id: string
  template_version_id: string
  revision_id: string
  revision: number
  project_id: string | null
  content_digest: string
  bindings: Array<{
    node_key: string
    runtime_id: string | null
    runtime_instance_id: string | null
    runtime_agent_release_id: string | null
    agent_release_id: string | null
    agent_id: string | null
    agent_name: string | null
    release_version: string | null
    resolved_spec_digest: string | null
    model_selection_mode: "exact" | "agent_preference" | null
    preferred_model_definition_id: string | null
    runtime_model_binding_id: string | null
    selection_source: string | null
    effective_spec_digest: string | null
  }>
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface WorkflowExecutionConfigurationDetail {
  template: {
    id: string
    name: string
    description: string | null
  }
  version: {
    id: string
    version: string
    manifest: Record<string, unknown>
  }
  nodes: Array<{
    id: string
    node_key: string
    name: string
    node_type: "human" | "agent" | "code"
    agent_release_id: string | null
    agent_role_key: string | null
    side_effecting: boolean
    position: number
  }>
  execution_configuration: WorkflowExecutionConfiguration | null
}

export interface WorkflowNodeDetail {
  node: WorkflowNodeInstance
  definition: {
    node_type: "human" | "agent" | "code"
    node_key: string
    name: string
    agent_role_key: string | null
    confirmation_mode: "process" | "result" | "none"
    skippable: boolean
    side_effecting: boolean
    input_schema: Record<string, unknown>
    output_schema: Record<string, unknown>
  }
  revisions: Array<Record<string, unknown>>
  executions: Array<
    Record<string, unknown> & {
      id: string
      attempt: number
      status: string
      error?: Record<string, unknown> | null
      external_state_proof?: Record<string, unknown> | null
    }
  >
  confirmations: Array<Record<string, unknown>>
  gates: Array<Record<string, unknown>>
  artifacts: Array<Record<string, unknown>>
  messages: ConversationMessage[]
  agent: {
    role_key: string | null
    agent_id: string
    agent_name: string | null
    agent_release_id: string
    release_version: string
    runtime_id: string
    resolved_spec_digest: string
  } | null
}

export interface WorkflowEvent {
  id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface WorkflowAttachment {
  id: string
  filename: string
  content_type: string
  size: number
  content_digest: string
  scan_status: string
  scan_details: Record<string, unknown>
  created_by: string | null
  created_at: string
}

export interface WorkflowInstance {
  id: string
  context_mode: "project" | "standalone"
  project_id: string | null
  template_version_id: string
  execution_configuration_revision_id: string | null
  workflow_slug: string
  application: {
    component_key: string
    route_slug: string
    build_digest: string
    shell_version: string
  }
  package_digest: string
  title: string
  status: string
  input: Record<string, unknown>
  runtime_resolution: Record<string, unknown>
  agent_bindings: Array<{
    role_key: string
    runtime_id: string
    agent_release_id: string
    agent_id: string | null
    agent_name: string | null
    release_version: string | null
    resolved_spec_digest: string
  }>
  project_context_snapshot: Record<string, unknown>
  nodes: WorkflowNodeInstance[]
  created_at: string
  updated_at: string
}

export interface WorkflowTemplateCatalogItem {
  id: string
  slug: string
  name: string
  description: string | null
  scope_type: "platform" | "namespace"
  application: {
    route_slug: string
    component_key: string
  } | null
  versions: Array<{
    version: {
      id: string
      version: string
      package_digest: string
      manifest: Record<string, unknown>
    }
    application: {
      route_slug: string
      component_key: string
      build_digest: string
      shell_version: string
    } | null
    enablement: { enabled: boolean; is_default: boolean }
    execution_configuration: WorkflowExecutionConfiguration | null
  }>
}

export interface SpecStandard {
  id: string
  scope_type: "platform" | "namespace"
  namespace_id: string | null
  slug: string
  name: string
  description: string | null
}

export interface SpecStandardVersion {
  id: string
  standard_id: string
  version: string
  manifest: Record<string, unknown>
  content_digest: string
  storage_ref: string | null
  status: "active" | "deprecated"
  created_at: string
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`
  }
  return JSON.stringify(value) ?? "null"
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  )
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("")
}

export const workspaceApi = {
  listProjects: async (includeArchived = false) =>
    (
      await api.get<{ data: ProjectSummary[]; count: number }>(
        "/api/v1/projects",
        { params: { include_archived: includeArchived } },
      )
    ).data,
  getProject: async (projectId: string) =>
    (await api.get<ProjectSummary>(`/api/v1/projects/${projectId}`)).data,
  createProject: async (body: Record<string, unknown>) =>
    (await api.post<ProjectSummary>("/api/v1/projects", body)).data,
  archiveProject: async (projectId: string) =>
    (
      await api.patch<ProjectSummary>(`/api/v1/projects/${projectId}`, {
        archive: true,
      })
    ).data,
  listRepositories: async (projectId: string) =>
    (
      await api.get<{ data: ProjectRepository[]; count: number }>(
        `/api/v1/projects/${projectId}/repositories`,
      )
    ).data,
  createRepository: async (projectId: string, body: Record<string, unknown>) =>
    (
      await api.post<ProjectRepository>(
        `/api/v1/projects/${projectId}/repositories`,
        body,
      )
    ).data,
  listSpecLocations: async (projectId: string) =>
    (
      await api.get<{ data: ProjectSpecLocation[]; count: number }>(
        `/api/v1/projects/${projectId}/spec-locations`,
      )
    ).data,
  createSpecLocation: async (
    projectId: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post<ProjectSpecLocation>(
        `/api/v1/projects/${projectId}/spec-locations`,
        body,
      )
    ).data,
  bindSpecStandard: async (
    projectId: string,
    locationId: string,
    standardVersionId: string,
  ) =>
    (
      await api.put(
        `/api/v1/projects/${projectId}/spec-locations/${locationId}/binding`,
        { standard_version_id: standardVersionId },
      )
    ).data,
  listConversationRuntimes: async () =>
    (
      await api.get<{ data: ConversationRuntime[]; count: number }>(
        "/api/v1/conversation-catalog/runtimes",
      )
    ).data,
  listConversationModels: async (runtimeId: string) =>
    (
      await api.get<{ data: ConversationModelCatalogItem[]; count: number }>(
        `/api/v1/conversation-catalog/runtimes/${runtimeId}/models`,
      )
    ).data,
  listConversationAgents: async (runtimeId: string) =>
    (
      await api.get<{ data: ConversationAgentCatalogItem[]; count: number }>(
        `/api/v1/conversation-catalog/runtimes/${runtimeId}/agents`,
      )
    ).data,
  listConversations: async () =>
    (
      await api.get<{ data: ConversationSummary[]; count: number }>(
        "/api/v1/conversations",
      )
    ).data,
  getConversation: async (conversationId: string) =>
    (
      await api.get<ConversationSummary>(
        `/api/v1/conversations/${conversationId}`,
      )
    ).data,
  createConversation: async (body: Record<string, unknown>) =>
    (
      await api.post<ConversationSummary>("/api/v1/conversations", body, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).data,
  updateConversationConfiguration: async (
    conversationId: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post<ConversationSummary>(
        `/api/v1/conversations/${conversationId}/configuration-revisions`,
        body,
      )
    ).data,
  listMessages: async (conversationId: string) =>
    (
      await api.get<{ data: ConversationMessage[]; count: number }>(
        `/api/v1/conversations/${conversationId}/messages`,
      )
    ).data,
  listDelegations: async (conversationId: string) =>
    (
      await api.get<{ data: AgentDelegation[]; count: number }>(
        `/api/v1/conversations/${conversationId}/delegations`,
      )
    ).data,
  streamConversationEvents: async (
    conversationId: string,
    lastEventId: number,
    onEvent: (event: ConversationEvent) => void,
    signal?: AbortSignal,
  ) => {
    const namespaceId = localStorage.getItem("selected_namespace_id") || ""
    const response = await fetch(
      `${client.getConfig().baseURL}/api/v1/conversations/${conversationId}/events`,
      {
        headers: {
          Accept: "text/event-stream",
          "Last-Event-ID": String(lastEventId),
          "X-Namespace-Id": namespaceId,
        },
        credentials: "include",
        signal,
      },
    )
    if (!response.ok || !response.body) {
      throw new Error(`会话事件流连接失败 (${response.status})`)
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
        if (data) onEvent(JSON.parse(data.slice(6)) as ConversationEvent)
      }
      if (done) break
    }
  },
  sendMessage: async (conversationId: string, body: Record<string, unknown>) =>
    (
      await api.post(`/api/v1/conversations/${conversationId}/messages`, body, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).data,
  uploadConversationAttachment: async (conversationId: string, file: File) => {
    const form = new FormData()
    form.append("file", file)
    return (
      await api.post<ConversationAttachment>(
        `/api/v1/conversations/${conversationId}/attachments`,
        form,
      )
    ).data
  },
  refreshContext: async (conversationId: string) =>
    (
      await api.post(
        `/api/v1/conversations/${conversationId}/context-snapshots`,
        {},
      )
    ).data,
  listWorkflowTemplates: async () =>
    (
      await api.get<{ data: WorkflowTemplateCatalogItem[]; count: number }>(
        "/api/v1/workflow-templates",
      )
    ).data,
  updateWorkflowEnablement: async (
    templateId: string,
    body: Record<string, unknown>,
  ) =>
    (await api.put(`/api/v1/workflow-templates/${templateId}/enablement`, body))
      .data,
  getWorkflowExecutionConfiguration: async (
    templateId: string,
    versionId: string,
  ) =>
    (
      await api.get<WorkflowExecutionConfigurationDetail>(
        `/api/v1/workflow-templates/${templateId}/versions/${versionId}/execution-configuration`,
      )
    ).data,
  updateWorkflowExecutionConfiguration: async (
    templateId: string,
    versionId: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.put<{
        execution_configuration: WorkflowExecutionConfiguration
      }>(
        `/api/v1/workflow-templates/${templateId}/versions/${versionId}/execution-configuration`,
        body,
      )
    ).data,
  precheckWorkflowExecutionConfiguration: async (
    templateId: string,
    versionId: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post<{
        passed: boolean
        errors: Array<Record<string, unknown>>
        resolved_bindings: Record<string, unknown>
      }>(
        `/api/v1/workflow-templates/${templateId}/versions/${versionId}/execution-configuration/precheck`,
        body,
      )
    ).data,
  listSpecStandards: async () =>
    (
      await api.get<{ data: SpecStandard[]; count: number }>(
        "/api/v1/spec-standards",
      )
    ).data,
  createSpecStandard: async (body: Record<string, unknown>) =>
    (await api.post<SpecStandard>("/api/v1/spec-standards", body)).data,
  createSpecStandardComplete: async (body: {
    scope_type: "namespace" | "platform"
    slug: string
    name: string
    description?: string | null
    version: string
    manifest: Record<string, unknown>
  }) =>
    (
      await api.post<{
        standard: SpecStandard
        version: SpecStandardVersion
      }>("/api/v1/spec-standards/complete", body)
    ).data,
  listSpecStandardVersions: async (standardId: string) =>
    (
      await api.get<{ data: SpecStandardVersion[]; count: number }>(
        `/api/v1/spec-standards/${standardId}/versions`,
      )
    ).data,
  publishSpecStandardVersion: async (
    standardId: string,
    body: {
      version: string
      manifest: Record<string, unknown>
      storage_ref?: string | null
    },
  ) => {
    const contentDigest = await sha256(canonicalJson(body.manifest))
    return (
      await api.post<SpecStandardVersion>(
        `/api/v1/spec-standards/${standardId}/versions`,
        { ...body, content_digest: contentDigest },
      )
    ).data
  },
  listWorkflowInstances: async (projectId: string) =>
    (
      await api.get<{ data: WorkflowInstance[]; count: number }>(
        `/api/v1/projects/${projectId}/workflow-instances`,
      )
    ).data,
  listVisibleWorkflowInstances: async (templateVersionId?: string) =>
    (
      await api.get<{ data: WorkflowInstance[]; count: number }>(
        "/api/v1/workflow-instances",
        {
          params: templateVersionId
            ? { template_version_id: templateVersionId }
            : undefined,
        },
      )
    ).data,
  listWorkflowTemplateInstances: async (templateId: string) =>
    (
      await api.get<{ data: WorkflowInstance[]; count: number }>(
        "/api/v1/workflow-instances",
        { params: { template_id: templateId } },
      )
    ).data,
  getWorkflowInstance: async (instanceId: string) =>
    (
      await api.get<WorkflowInstance>(
        `/api/v1/workflow-instances/${instanceId}`,
      )
    ).data,
  getWorkflowNode: async (instanceId: string, nodeKey: string) =>
    (
      await api.get<WorkflowNodeDetail>(
        `/api/v1/workflow-instances/${instanceId}/nodes/${nodeKey}`,
      )
    ).data,
  listWorkflowEvents: async (instanceId: string, afterSequence = 0) =>
    (
      await api.get<{ data: WorkflowEvent[]; count: number }>(
        `/api/v1/workflow-instances/${instanceId}/events`,
        { params: { after_sequence: afterSequence } },
      )
    ).data,
  listWorkflowAttachments: async (instanceId: string) =>
    (
      await api.get<{ data: WorkflowAttachment[]; count: number }>(
        `/api/v1/workflow-instances/${instanceId}/attachments`,
      )
    ).data,
  uploadWorkflowAttachment: async (instanceId: string, file: File) => {
    const form = new FormData()
    form.append("file", file)
    return (
      await api.post<WorkflowAttachment>(
        `/api/v1/workflow-instances/${instanceId}/attachments`,
        form,
      )
    ).data
  },
  streamWorkflowEvents: async (
    instanceId: string,
    lastEventId: number,
    onEvent: (event: WorkflowEvent) => void,
    signal?: AbortSignal,
  ) => {
    const namespaceId = localStorage.getItem("selected_namespace_id") || ""
    const response = await fetch(
      `${client.getConfig().baseURL}/api/v1/workflow-instances/${instanceId}/events`,
      {
        headers: {
          Accept: "text/event-stream",
          "Last-Event-ID": String(lastEventId),
          "X-Namespace-Id": namespaceId,
        },
        credentials: "include",
        signal,
      },
    )
    if (!response.ok || !response.body) {
      throw new Error(`Workflow 事件流连接失败 (${response.status})`)
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
        if (data) onEvent(JSON.parse(data.slice(6)) as WorkflowEvent)
      }
      if (done) break
    }
  },
  createWorkflowInstance: async (
    projectId: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post<WorkflowInstance>(
        `/api/v1/projects/${projectId}/workflow-instances`,
        body,
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
  createVisibleWorkflowInstance: async (body: Record<string, unknown>) =>
    (
      await api.post<WorkflowInstance>("/api/v1/workflow-instances", body, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      })
    ).data,
  submitWorkflowNode: async (
    instanceId: string,
    nodeKey: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post(
        `/api/v1/workflow-instances/${instanceId}/nodes/${nodeKey}/submit`,
        body,
      )
    ).data,
  confirmWorkflowNode: async (
    instanceId: string,
    nodeKey: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post(
        `/api/v1/workflow-instances/${instanceId}/nodes/${nodeKey}/confirm`,
        body,
      )
    ).data,
  skipWorkflowNode: async (
    instanceId: string,
    nodeKey: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post(
        `/api/v1/workflow-instances/${instanceId}/nodes/${nodeKey}/skip`,
        body,
      )
    ).data,
  retryWorkflowNode: async (
    instanceId: string,
    nodeKey: string,
    expectedRevision: number,
  ) =>
    (
      await api.post(
        `/api/v1/workflow-instances/${instanceId}/nodes/${nodeKey}/retry`,
        { expected_revision: expectedRevision },
      )
    ).data,
  sendWorkflowNodeMessage: async (
    instanceId: string,
    nodeKey: string,
    expectedRevision: number,
    content: string,
  ) =>
    (
      await api.post(
        `/api/v1/workflow-instances/${instanceId}/nodes/${nodeKey}/messages`,
        { expected_revision: expectedRevision, content },
      )
    ).data,
  resolveWorkflowExternalState: async (
    instanceId: string,
    nodeKey: string,
    body: Record<string, unknown>,
  ) =>
    (
      await api.post(
        `/api/v1/workflow-instances/${instanceId}/nodes/${nodeKey}/external-state-resolution`,
        body,
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      )
    ).data,
  cancelWorkflowInstance: async (instanceId: string) =>
    (await api.post(`/api/v1/workflow-instances/${instanceId}/cancel`, {}))
      .data,
}
