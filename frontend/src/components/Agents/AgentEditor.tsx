import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  agentsApi,
  modelDefinitionsApi,
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
  const { data: modelDefinitions } = useQuery({
    queryKey: ["model-definitions"],
    queryFn: modelDefinitionsApi.list,
  })

  // Local editable state, initialized from draft.
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null)
  const [systemPrompt, setSystemPrompt] = useState("")
  const [preferredModelDefinitionId, setPreferredModelDefinitionId] =
    useState("")
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
      setSystemPrompt(draft.system_prompt)
      setPreferredModelDefinitionId(draft.preferred_model_definition_id ?? "")
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
        typeof draft.execution_policy.timeout_seconds === "number"
          ? draft.execution_policy.timeout_seconds
          : 3600,
      )
      setWorkingDirectoryStrategy(
        typeof draft.execution_policy.working_directory_strategy === "string"
          ? draft.execution_policy.working_directory_strategy
          : "inherit",
      )
    }
  }, [draft])

  const saveMutation = useMutation({
    mutationFn: () =>
      agentsApi.saveDraft(agentId, {
        expected_revision: expectedRevision!,
        preferred_model_definition_id: preferredModelDefinitionId || null,
        system_prompt: systemPrompt,
        execution_policy: {
          timeout_seconds: timeoutSeconds,
          working_directory_strategy: workingDirectoryStrategy,
        },
        config: {},
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
          <p className="text-sm text-muted-foreground">
            Agent 标识：<span className="font-mono">{agent.slug}</span>
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
          <TabsTrigger value="model">模型偏好与策略</TabsTrigger>
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
              <CardTitle>模型偏好与系统提示词</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>稳定模型偏好</Label>
                <Select
                  value={preferredModelDefinitionId}
                  onValueChange={(value) => {
                    setPreferredModelDefinitionId(value)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择稳定模型身份" />
                  </SelectTrigger>
                  <SelectContent>
                    {modelDefinitions?.data
                      .filter((model) => model.enabled)
                      .map((model) => (
                        <SelectItem key={model.id} value={model.id}>
                          {model.display_name ?? model.model_key} ·{" "}
                          {model.provider_family}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Agent 不绑定 Runtime 或路由。实际模型由 Conversation、Agent
                  组或 Workflow 的 exact / agent_preference 配置解析并冻结。
                </p>
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
