import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  agentsApi,
  harnessProfilesApi,
  type ValidationResult,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

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

  // Local editable state, initialized from draft.
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null)
  const [harnessProfileId, setHarnessProfileId] = useState<string>("")
  const [systemPrompt, setSystemPrompt] = useState("")
  const [modelId, setModelId] = useState("")
  const [providerConfigId, setProviderConfigId] = useState("")
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

  const saveMutation = useMutation({
    mutationFn: () =>
      agentsApi.saveDraft(agentId, {
        expected_revision: expectedRevision!,
        harness_profile_id: harnessProfileId || null,
        provider_config_id: providerConfigId || null,
        model_id: modelId || null,
        system_prompt: systemPrompt,
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
      if (result.status === "validated") {
        showSuccessToast("校验通过")
      } else {
        showErrorToast(
          `校验失败：${result.errors.length} 个错误，${result.warnings.length} 个警告`
        )
      }
      refetchDraft()
      queryClient.invalidateQueries({ queryKey: ["agents"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const reload = () => refetchDraft()

  if (!agent || !draft) return <p className="text-muted-foreground">加载中…</p>

  const archived = agent.status === "archived"

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{agent.name}</h1>
          <p className="text-sm text-muted-foreground font-mono">{agent.slug}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge>revision {draft.revision}</Badge>
          <Badge>{draft.validation_status}</Badge>
          {agent.status === "archived" && <Badge variant="destructive">已归档</Badge>}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="model">模型与提示词</TabsTrigger>
          <TabsTrigger value="harness">Harness</TabsTrigger>
          <TabsTrigger value="capabilities" disabled>
            能力
          </TabsTrigger>
          <TabsTrigger value="publish" disabled>
            校验与发布
          </TabsTrigger>
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
                  value={agent.name}
                  disabled={!canManage || archived}
                  onChange={() => {}}
                />
              </div>
              <div className="space-y-2">
                <Label>说明</Label>
                <Input
                  value={agent.description ?? ""}
                  disabled={!canManage || archived}
                  onChange={() => {}}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                名称与说明请通过保存草稿外的编辑入口修改（概览编辑在后续完善）。
              </p>
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
                <Label>Provider 配置</Label>
                <Input
                  value={providerConfigId}
                  onChange={(e) => {
                    setProviderConfigId(e.target.value)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                  placeholder="provider config UUID"
                />
              </div>
              <div className="space-y-2">
                <Label>模型 ID</Label>
                <Input
                  value={modelId}
                  onChange={(e) => {
                    setModelId(e.target.value)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                  placeholder="claude-sonnet-4-20250514"
                />
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
          <Card>
            <CardContent className="py-10 text-center text-muted-foreground">
              能力页签将在后续阶段启用（Skill / Tool / MCP / Plugin 绑定）。
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="publish">
          <Card>
            <CardContent className="py-10 text-center text-muted-foreground">
              校验与发布将在后续阶段启用（ResolvedAgentSpec / Agent Release）。
            </CardContent>
          </Card>
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
    </div>
  )
}
