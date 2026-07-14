import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"
import {
  type ConversationRuntime,
  type WorkflowExecutionConfigurationDetail,
  workspaceApi,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export const Route = createFileRoute(
  "/_layout/system/workflows_/$templateId/versions/$versionId/configuration",
)({
  component: WorkflowExecutionConfigurationPage,
})

type NodeBinding = {
  runtime_id: string
  agent_release_id: string | null
}

function RuntimeLabel({ runtime }: { runtime: ConversationRuntime }) {
  return (
    <>
      {runtime.runtime_type === "platform" ? "平台" : "节点"} ·{" "}
      {runtime.model_id}
    </>
  )
}

function NodeConfiguration({
  node,
  runtime,
  runtimes,
  binding,
  onChange,
}: {
  node: WorkflowExecutionConfigurationDetail["nodes"][number]
  runtime: ConversationRuntime | undefined
  runtimes: ConversationRuntime[]
  binding: NodeBinding
  onChange: (binding: NodeBinding) => void
}) {
  const agents = useQuery({
    queryKey: ["conversation-agents", binding.runtime_id],
    queryFn: () => workspaceApi.listConversationAgents(binding.runtime_id),
    enabled: node.node_type === "agent" && Boolean(binding.runtime_id),
  })
  const activeAgents = agents.data?.data.filter((agent) => agent.active) || []

  if (node.node_type === "human") {
    return (
      <Card>
        <CardContent className="flex items-center justify-between p-4">
          <div>
            <p className="font-medium">{node.name}</p>
            <p className="text-sm text-muted-foreground">
              人工节点的处理方式由流程代码定义。
            </p>
          </div>
          <Badge variant="outline">人工节点</Badge>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">{node.name}</CardTitle>
          <div className="flex gap-2">
            <Badge variant="outline">
              {node.node_type === "agent" ? "Agent 节点" : "代码节点"}
            </Badge>
            {node.side_effecting && (
              <Badge variant="destructive">有外部副作用</Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label>运行环境</Label>
          <Select
            value={binding.runtime_id}
            onValueChange={(runtimeId) =>
              onChange({ runtime_id: runtimeId, agent_release_id: null })
            }
          >
            <SelectTrigger>
              <SelectValue placeholder="选择可运行此节点的环境" />
            </SelectTrigger>
            <SelectContent>
              {runtimes.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  <RuntimeLabel runtime={item} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {runtime && !runtime.compatible && (
            <p className="text-xs text-destructive">
              当前环境已不兼容，请重新选择后保存。
            </p>
          )}
        </div>
        {node.node_type === "agent" ? (
          <div className="space-y-2">
            <Label>负责的 Agent</Label>
            <Select
              value={binding.agent_release_id || ""}
              onValueChange={(releaseId) =>
                onChange({ ...binding, agent_release_id: releaseId })
              }
              disabled={!binding.runtime_id || agents.isPending}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    binding.runtime_id
                      ? "选择已发布的 Agent"
                      : "请先选择运行环境"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {activeAgents.map((agent) => (
                  <SelectItem key={agent.release_id} value={agent.release_id}>
                    {agent.agent_name} · v{agent.release_version}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {binding.runtime_id &&
              !agents.isPending &&
              !activeAgents.length && (
                <p className="text-xs text-destructive">
                  该运行环境没有可用的 Agent 发布版本。
                </p>
              )}
          </div>
        ) : (
          <div className="space-y-2">
            <Label>执行逻辑</Label>
            <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
              由流程包中的节点代码固定，不在此处编辑。
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function WorkflowExecutionConfigurationPage() {
  const { templateId, versionId } = Route.useParams()
  const queryClient = useQueryClient()
  const [loadedRevisionId, setLoadedRevisionId] = useState<string | null>(null)
  const [projectId, setProjectId] = useState("")
  const [bindings, setBindings] = useState<Record<string, NodeBinding>>({})
  const detail = useQuery({
    queryKey: ["workflow-execution-configuration", templateId, versionId],
    queryFn: () =>
      workspaceApi.getWorkflowExecutionConfiguration(templateId, versionId),
  })
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => workspaceApi.listProjects(),
  })
  const runtimes = useQuery({
    queryKey: ["conversation-runtimes"],
    queryFn: workspaceApi.listConversationRuntimes,
  })

  useEffect(() => {
    if (!detail.data) return
    const revisionId = detail.data.execution_configuration?.revision_id || "new"
    if (loadedRevisionId === revisionId) return
    setLoadedRevisionId(revisionId)
    setProjectId(detail.data.execution_configuration?.project_id || "")
    setBindings(
      Object.fromEntries(
        detail.data.execution_configuration?.bindings.map((binding) => [
          binding.node_key,
          {
            runtime_id: binding.runtime_id,
            agent_release_id: binding.agent_release_id,
          },
        ]) || [],
      ),
    )
  }, [detail.data, loadedRevisionId])

  const projectMode = String(
    detail.data?.version.manifest.project_mode || "required",
  )
  const executableNodes = detail.data?.nodes.filter(
    (node) => node.node_type !== "human",
  )
  const configurationComplete = useMemo(
    () =>
      Boolean(detail.data) &&
      (projectMode !== "required" || Boolean(projectId)) &&
      Boolean(
        executableNodes?.every((node) => {
          const binding = bindings[node.node_key]
          return (
            Boolean(binding?.runtime_id) &&
            (node.node_type !== "agent" || Boolean(binding.agent_release_id))
          )
        }),
      ),
    [bindings, detail.data, executableNodes, projectId, projectMode],
  )
  const save = useMutation({
    mutationFn: () =>
      workspaceApi.updateWorkflowExecutionConfiguration(templateId, versionId, {
        expected_revision: detail.data?.execution_configuration?.revision || 0,
        project_id: projectId || null,
        node_bindings: Object.fromEntries(
          (executableNodes || []).map((node) => [
            node.node_key,
            bindings[node.node_key],
          ]),
        ),
      }),
    onSuccess: async () => {
      setLoadedRevisionId(null)
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["workflow-execution-configuration", templateId, versionId],
        }),
        queryClient.invalidateQueries({ queryKey: ["workflow-templates"] }),
      ])
      toast.success("执行配置已保存为新的修订")
    },
    onError: () =>
      toast.error("配置未通过校验，请检查项目、运行环境和 Agent 是否仍然可用"),
  })

  if (detail.isPending) {
    return <p className="text-sm text-muted-foreground">正在读取流程配置…</p>
  }
  if (!detail.data) {
    return (
      <p className="text-sm text-destructive">流程模板版本不存在或不可见。</p>
    )
  }
  const compatibleRuntimes =
    runtimes.data?.data.filter((runtime) => runtime.compatible) || []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Button variant="link" className="h-auto px-0" asChild>
            <Link to="/system/workflows">返回 Workflow 模板</Link>
          </Button>
          <h1 className="text-2xl font-bold">{detail.data.template.name}</h1>
          <p className="text-muted-foreground">
            配置版本 {detail.data.version.version}{" "}
            的项目及各节点执行资源。节点定义与执行逻辑仍由代码固定。
          </p>
        </div>
        <Badge variant="secondary">
          {detail.data.execution_configuration
            ? `当前修订 r${detail.data.execution_configuration.revision}`
            : "尚未配置"}
        </Badge>
      </div>

      {projectMode !== "none" && (
        <Card>
          <CardHeader>
            <CardTitle>项目上下文</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Label>
              项目{projectMode === "required" ? "（必选）" : "（可选）"}
            </Label>
            <Select
              value={
                projectId || (projectMode === "optional" ? "no-project" : "")
              }
              onValueChange={(value) =>
                setProjectId(value === "no-project" ? "" : value)
              }
            >
              <SelectTrigger className="max-w-xl">
                <SelectValue placeholder="选择流程实例使用的项目配置" />
              </SelectTrigger>
              <SelectContent>
                {projectMode === "optional" && (
                  <SelectItem value="no-project">不关联项目</SelectItem>
                )}
                {projects.data?.data.map((project) => (
                  <SelectItem key={project.id} value={project.id}>
                    {project.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              新实例会冻结当前项目配置；已有实例不会随本页修改而变化。
            </p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold">流程节点</h2>
          <p className="text-sm text-muted-foreground">
            为每个可执行节点选择准确的运行环境；Agent
            节点还需选择一个已发布并激活的 Agent。
          </p>
        </div>
        {detail.data.nodes.map((node) => (
          <NodeConfiguration
            key={node.id}
            node={node}
            runtime={runtimes.data?.data.find(
              (item) => item.id === bindings[node.node_key]?.runtime_id,
            )}
            runtimes={compatibleRuntimes}
            binding={
              bindings[node.node_key] || {
                runtime_id: "",
                agent_release_id: null,
              }
            }
            onChange={(binding) =>
              setBindings((current) => ({
                ...current,
                [node.node_key]: binding,
              }))
            }
          />
        ))}
      </div>

      <div className="flex justify-end">
        <Button
          disabled={!configurationComplete || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "正在校验并保存…" : "校验并保存新修订"}
        </Button>
      </div>
    </div>
  )
}
