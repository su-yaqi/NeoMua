import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  agentsApi,
  harnessProfilesApi,
  tenantApi,
  type ValidationResult,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import AgentCapabilities from "./AgentCapabilities"
import AgentReleasePanel from "./AgentReleasePanel"

export default function AgentEditor({
  agentId,
  canManage,
}: {
  agentId: string
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const { data: agent } = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => agentsApi.get(agentId),
  })
  const { data: draft, refetch: refetchDraft } = useQuery({
    queryKey: ["agent-draft", agentId],
    queryFn: () => agentsApi.getDraft(agentId),
  })
  const { data: profiles } = useQuery({
    queryKey: ["harness-profiles"],
    queryFn: harnessProfilesApi.list,
  })
  const { data: providerConfigs } = useQuery({
    queryKey: ["llm-provider-configs"],
    queryFn: tenantApi.readLlmProviderConfigs,
  })

  // Local editable state, initialized from draft.
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null)
  const [harnessProfileId, setHarnessProfileId] = useState<string>("")
  const [systemPrompt, setSystemPrompt] = useState("")
  const [modelId, setModelId] = useState("")
  const [providerConfigId, setProviderConfigId] = useState("")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [timeoutSeconds, setTimeoutSeconds] = useState(3600)
  const [workingDirectoryStrategy, setWorkingDirectoryStrategy] =
    useState("inherit")
  const [lastValidation, setLastValidation] = useState<ValidationResult | null>(
    null,
  )
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (draft) {
      setExpectedRevision(draft.revision)
      setHarnessProfileId(draft.harness_profile_id ?? "")
      setSystemPrompt(draft.system_prompt)
      setModelId(draft.model_id ?? "")
      setProviderConfigId(draft.provider_config_id ?? "")
      setDirty(false)
    }
  }, [draft])

  useEffect(() => {
    if (agent) {
      setName(agent.name)
      setDescription(agent.description ?? "")
    }
  }, [agent])

  useEffect(() => {
    if (draft) {
      setTimeoutSeconds(
        typeof draft.config.timeout_seconds === "number"
          ? draft.config.timeout_seconds
          : 3600,
      )
      setWorkingDirectoryStrategy(
        typeof draft.config.working_directory_strategy === "string"
          ? draft.config.working_directory_strategy
          : "inherit",
      )
    }
  }, [draft])

  const saveMutation = useMutation({
    mutationFn: () =>
      agentsApi.saveDraft(agentId, {
        expected_revision: expectedRevision!,
        harness_profile_id: harnessProfileId || null,
        provider_config_id: providerConfigId || null,
        model_id: modelId || null,
        system_prompt: systemPrompt,
        config: {
          timeout_seconds: timeoutSeconds,
          working_directory_strategy: workingDirectoryStrategy,
        },
      }),
    onSuccess: () => {
      showSuccessToast("草稿已保存")
      setDirty(false)
      refetchDraft()
      queryClient.invalidateQueries({ queryKey: ["agents"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const validateMutation = useMutation({
    mutationFn: () => agentsApi.validate(agentId),
    onSuccess: (result: ValidationResult) => {
      setLastValidation(result)
      if (result.status === "validated") {
        showSuccessToast("校验通过")
      } else {
        showErrorToast(
          `校验失败：${result.errors.length} 个错误，${result.warnings.length} 个警告`,
        )
      }
      refetchDraft()
      queryClient.invalidateQueries({ queryKey: ["agents"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const identityMutation = useMutation({
    mutationFn: () =>
      agentsApi.update(agentId, { name, description: description || null }),
    onSuccess: () => {
      showSuccessToast("Agent 信息已更新")
      queryClient.invalidateQueries({ queryKey: ["agent", agentId] })
      queryClient.invalidateQueries({ queryKey: ["agents"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const statusMutation = useMutation({
    mutationFn: (status: "active" | "archived") =>
      agentsApi.update(agentId, { status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", agentId] })
      queryClient.invalidateQueries({ queryKey: ["agents"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteMutation = useMutation({
    mutationFn: () => agentsApi.delete(agentId),
    onSuccess: () => {
      showSuccessToast("Agent 已删除")
      window.location.assign("/system/agents")
    },
    onError: handleError.bind(showErrorToast),
  })

  const reload = () => refetchDraft()

  if (!agent || !draft) return <p className="text-muted-foreground">加载中…</p>

  const archived = agent.status === "archived"
  const validationResult =
    lastValidation ??
    (draft.validation_result as unknown as ValidationResult | null)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{agent.name}</h1>
          <p className="text-sm text-muted-foreground font-mono">
            {agent.slug}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge>revision {draft.revision}</Badge>
          <Badge>{draft.validation_status}</Badge>
          {agent.status === "archived" && (
            <Badge variant="destructive">已归档</Badge>
          )}
          {canManage && (
            <Button
              variant="outline"
              onClick={() =>
                statusMutation.mutate(archived ? "active" : "archived")
              }
              disabled={statusMutation.isPending}
            >
              {archived ? "恢复" : "归档"}
            </Button>
          )}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="model">模型与提示词</TabsTrigger>
          <TabsTrigger value="harness">Harness</TabsTrigger>
          <TabsTrigger value="capabilities">能力</TabsTrigger>
          <TabsTrigger value="publish">校验与发布</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <Card>
            <CardHeader>
              <CardTitle>概览</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>名称</Label>
                <Input
                  value={name}
                  disabled={!canManage || archived}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>说明</Label>
                <Input
                  value={description}
                  disabled={!canManage || archived}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </div>
              {canManage && !archived && (
                <Button
                  onClick={() => identityMutation.mutate()}
                  disabled={!name.trim() || identityMutation.isPending}
                >
                  保存基本信息
                </Button>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="model">
          <Card>
            <CardHeader>
              <CardTitle>模型与系统提示词</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>模型</Label>
                <Select
                  value={
                    providerConfigId && modelId
                      ? `${providerConfigId}::${modelId}`
                      : ""
                  }
                  onValueChange={(value) => {
                    const separator = value.indexOf("::")
                    setProviderConfigId(value.slice(0, separator))
                    setModelId(value.slice(separator + 2))
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择当前空间已启用的模型" />
                  </SelectTrigger>
                  <SelectContent>
                    {providerConfigs?.data
                      .filter((config) => config.enabled)
                      .flatMap((config) =>
                        config.models
                          .filter((model) => model.is_enabled)
                          .map((model) => (
                            <SelectItem
                              key={`${config.id}::${model.model_id}`}
                              value={`${config.id}::${model.model_id}`}
                            >
                              {config.config_name} /{" "}
                              {model.display_name ?? model.model_id}
                            </SelectItem>
                          )),
                      )}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>系统提示词</Label>
                <textarea
                  className="min-h-40 w-full rounded-md border bg-background p-3"
                  value={systemPrompt}
                  onChange={(e) => {
                    setSystemPrompt(e.target.value)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                  rows={10}
                />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>超时覆盖（秒）</Label>
                  <Input
                    type="number"
                    min={1}
                    max={3600}
                    value={timeoutSeconds}
                    onChange={(event) => {
                      setTimeoutSeconds(Number(event.target.value))
                      setDirty(true)
                    }}
                    disabled={!canManage || archived}
                  />
                </div>
                <div className="space-y-2">
                  <Label>工作目录策略</Label>
                  <Select
                    value={workingDirectoryStrategy}
                    onValueChange={(value) => {
                      setWorkingDirectoryStrategy(value)
                      setDirty(true)
                    }}
                    disabled={!canManage || archived}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">继承运行时工作区</SelectItem>
                      <SelectItem value="require_root">
                        要求预登记根目录
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="harness">
          <Card>
            <CardHeader>
              <CardTitle>Harness Profile</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Harness Profile</Label>
                <Select
                  value={harnessProfileId}
                  onValueChange={(v) => {
                    setHarnessProfileId(v)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择 Harness Profile" />
                  </SelectTrigger>
                  <SelectContent>
                    {profiles?.data
                      .filter((p) => !p.archived)
                      .map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name} ({p.harness_type})
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="capabilities">
          <AgentCapabilities
            agentId={agentId}
            canManage={canManage && !archived}
          />
        </TabsContent>

        <TabsContent value="publish">
          <AgentReleasePanel
            agentId={agentId}
            revision={draft.revision}
            validatedRevision={draft.validated_revision}
            canManage={canManage && !archived}
          />
        </TabsContent>
      </Tabs>

      {canManage && !archived && (
        <div className="flex items-center gap-2">
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={!dirty || saveMutation.isPending}
          >
            保存草稿
          </Button>
          <Button
            variant="secondary"
            onClick={() => validateMutation.mutate()}
            disabled={validateMutation.isPending}
          >
            校验草稿
          </Button>
          <Button variant="ghost" onClick={reload}>
            重新加载
          </Button>
          {dirty && (
            <span className="text-sm text-yellow-600">有未保存的修改</span>
          )}
        </div>
      )}
      {validationResult && (
        <Card>
          <CardHeader>
            <CardTitle>校验结果</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {(validationResult.errors ?? []).map((item) => (
              <p key={`${item.code}-${item.field}`} className="text-red-600">
                [{item.code}] {item.field}: {item.message}
              </p>
            ))}
            {(validationResult.warnings ?? []).map((item) => (
              <p key={`${item.code}-${item.field}`} className="text-yellow-700">
                [{item.code}] {item.field}: {item.message}
              </p>
            ))}
            {(validationResult.target_compatibility ?? []).map((target) => (
              <p key={target.runtime_profile_id}>
                {target.runtime_type} / {target.runtime_profile_id}:{" "}
                {target.compatible === null
                  ? "unknown"
                  : target.compatible
                    ? "compatible"
                    : "incompatible"}
                {target.cli_version ? ` · CLI ${target.cli_version}` : ""}
                {target.sdk_version ? ` · SDK ${target.sdk_version}` : ""}
                {target.harness_version
                  ? ` · Harness ${target.harness_version}`
                  : ""}
              </p>
            ))}
          </CardContent>
        </Card>
      )}
      {canManage && !archived && (
        <Button
          variant="destructive"
          onClick={() => {
            if (
              window.confirm("确认删除这个尚未发布的 Agent？此操作不可撤销。")
            ) {
              deleteMutation.mutate()
            }
          }}
          disabled={deleteMutation.isPending}
        >
          删除 Agent
        </Button>
      )}
    </div>
  )
}
