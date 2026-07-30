import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { toast } from "sonner"
import {
  type WorkflowNodeDetail,
  type WorkflowNodeInstance,
  workspaceApi,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

const flowNodes = [
  ["requirements_communication", "需求沟通", "Agent"],
  ["requirements_design", "需求设计", "Agent"],
  ["technical_solution", "技术方案", "Agent"],
  ["backend_development", "后端开发", "Agent"],
  ["frontend_development", "前端开发", "Agent"],
  ["test_case_design", "测试用例", "Agent"],
  ["test_environment_deployment", "测试环境部署", "Agent"],
  ["testing", "测试", "Agent"],
  ["acceptance", "验收", "人"],
  ["go_live", "上线", "人"],
] as const

const statusLabels: Record<string, string> = {
  inactive: "未开始",
  ready: "准备中",
  running: "执行中",
  waiting_confirmation: "等待人工",
  completed: "已完成",
  update_required: "待更新",
  blocked: "已阻塞",
  failed: "失败",
  skipped: "已跳过",
  needs_manual_resolution: "需人工处理",
}

function statusVariant(status: string) {
  if (["blocked", "failed", "needs_manual_resolution"].includes(status)) {
    return "destructive" as const
  }
  if (["completed", "skipped"].includes(status)) return "secondary" as const
  return "outline" as const
}

function JsonPreview({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function WebPlatformDevelopmentTask({
  instanceId,
}: {
  instanceId: string
}) {
  const [selectedNodeKey, setSelectedNodeKey] = useState("")
  const task = useQuery({
    queryKey: ["workflow-task", instanceId],
    queryFn: () => workspaceApi.getWorkflowInstance(instanceId),
    refetchInterval: 2000,
  })
  const activeNode = task.data?.nodes.find(
    (node) => !["inactive", "completed", "skipped"].includes(node.status),
  )
  useEffect(() => {
    if (!selectedNodeKey && task.data?.nodes.length) {
      setSelectedNodeKey(
        activeNode?.node_key ||
          task.data.nodes[task.data.nodes.length - 1]?.node_key ||
          "",
      )
    }
  }, [activeNode?.node_key, selectedNodeKey, task.data?.nodes])
  if (!task.data) return <p>正在加载 Web 平台开发流程…</p>
  const selectedNode = task.data.nodes.find(
    (node) => node.node_key === selectedNodeKey,
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{task.data.title}</h1>
          <p className="text-sm text-muted-foreground">
            项目 {task.data.project_id} · 项目与 Runtime 已按创建时配置冻结
          </p>
        </div>
        <Badge variant={statusVariant(task.data.status)}>
          {statusLabels[task.data.status] || task.data.status}
        </Badge>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>流程进度</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex min-w-max items-start overflow-x-auto pb-3">
            {flowNodes.map(([nodeKey, name, owner], index) => {
              const node = task.data.nodes.find(
                (item) => item.node_key === nodeKey,
              )
              const selected = nodeKey === selectedNodeKey
              return (
                <div key={nodeKey} className="flex items-center">
                  <button
                    type="button"
                    className={`w-32 rounded-lg border p-3 text-left transition-colors ${
                      selected
                        ? "border-primary bg-primary/5"
                        : "hover:border-primary/50"
                    }`}
                    onClick={() => setSelectedNodeKey(nodeKey)}
                  >
                    <span className="block text-xs text-muted-foreground">
                      {index + 1}. {owner}
                    </span>
                    <span className="mt-1 block text-sm font-medium">
                      {name}
                    </span>
                    <Badge
                      className="mt-2"
                      variant={statusVariant(node?.status || "inactive")}
                    >
                      {statusLabels[node?.status || "inactive"]}
                    </Badge>
                  </button>
                  {index < flowNodes.length - 1 && (
                    <div className="mx-2 h-px w-8 bg-border" />
                  )}
                </div>
              )
            })}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            点击节点只切换下方查看内容，不会改变流程执行状态。
          </p>
        </CardContent>
      </Card>
      {selectedNode ? (
        <WorkflowNodePanel instanceId={instanceId} node={selectedNode} />
      ) : (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">
            请选择一个流程节点。
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function WorkflowNodePanel({
  instanceId,
  node,
}: {
  instanceId: string
  node: WorkflowNodeInstance
}) {
  const detail = useQuery({
    queryKey: ["workflow-node", instanceId, node.node_key],
    queryFn: () => workspaceApi.getWorkflowNode(instanceId, node.node_key),
    refetchInterval: 2000,
  })
  if (!detail.data) {
    return (
      <Card>
        <CardContent className="py-8">正在加载节点详情…</CardContent>
      </Card>
    )
  }
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle>{detail.data.definition.name}</CardTitle>
          <Badge variant={statusVariant(node.status)}>
            {statusLabels[node.status] || node.status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="agent">
          <TabsList>
            <TabsTrigger value="agent">Agent 信息</TabsTrigger>
            <TabsTrigger value="artifacts">产出物清单与预览</TabsTrigger>
            <TabsTrigger value="chat">Agent 聊天</TabsTrigger>
          </TabsList>
          <TabsContent value="agent" className="pt-4">
            <AgentInformation
              instanceId={instanceId}
              node={node}
              detail={detail.data}
            />
          </TabsContent>
          <TabsContent value="artifacts" className="pt-4">
            <Deliverables
              instanceId={instanceId}
              node={node}
              detail={detail.data}
            />
          </TabsContent>
          <TabsContent value="chat" className="pt-4">
            <AgentChat
              instanceId={instanceId}
              node={node}
              detail={detail.data}
            />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

function AgentInformation({
  instanceId,
  node,
  detail,
}: {
  instanceId: string
  node: WorkflowNodeInstance
  detail: WorkflowNodeDetail
}) {
  const queryClient = useQueryClient()
  const [evidence, setEvidence] = useState("")
  const uncertainExecution = detail.executions.find(
    (execution) => execution.status === "needs_manual_resolution",
  )
  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ["workflow-task", instanceId],
    })
    void queryClient.invalidateQueries({
      queryKey: ["workflow-node", instanceId, node.node_key],
    })
  }
  const resolveExternalState = useMutation({
    mutationFn: () =>
      workspaceApi.resolveWorkflowExternalState(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        execution_id: uncertainExecution?.id,
        conclusion: "not_started",
        allow_retry: true,
        evidence: { operator_statement: evidence },
        rationale: evidence,
      }),
    onSuccess: refresh,
    onError: () => toast.error("外部状态结论未被接受"),
  })
  const retry = useMutation({
    mutationFn: () =>
      workspaceApi.retryWorkflowNode(
        instanceId,
        node.node_key,
        node.expected_revision,
      ),
    onSuccess: refresh,
    onError: () => toast.error("重试被阻止，请先核验外部状态"),
  })

  if (!detail.agent) {
    return (
      <div className="space-y-2 text-sm">
        <p>该节点由人员负责，不绑定 Agent。</p>
        <p className="text-muted-foreground">
          人工操作只通过明确提交推进，不会自动执行验收或生产上线。
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <div className="grid gap-3 text-sm md:grid-cols-2">
        <p>
          <span className="text-muted-foreground">Agent：</span>
          {detail.agent.agent_name || detail.agent.agent_id}
        </p>
        <p>
          <span className="text-muted-foreground">角色：</span>
          {detail.agent.role_key}
        </p>
        <p>
          <span className="text-muted-foreground">固定版本：</span>v
          {detail.agent.release_version}
        </p>
        <p>
          <span className="text-muted-foreground">Runtime：</span>
          {detail.agent.runtime_id}
        </p>
        <p className="break-all md:col-span-2">
          <span className="text-muted-foreground">Spec 摘要：</span>
          {detail.agent.resolved_spec_digest}
        </p>
      </div>
      {uncertainExecution && (
        <div className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <p className="text-sm font-medium">
            外部状态证明缺失，流程已安全阻断
          </p>
          <p className="text-xs text-muted-foreground">
            只有在你已经核验部署确实未开始时，才可记录证据并允许重试。
          </p>
          <Input
            value={evidence}
            onChange={(event) => setEvidence(event.target.value)}
            placeholder="填写核验方式与结果"
          />
          <Button
            variant="outline"
            disabled={!evidence.trim() || resolveExternalState.isPending}
            onClick={() => resolveExternalState.mutate()}
          >
            确认未开始并允许重试
          </Button>
        </div>
      )}
      {["failed", "blocked"].includes(node.status) && !uncertainExecution && (
        <Button
          variant="outline"
          disabled={retry.isPending}
          onClick={() => retry.mutate()}
        >
          重试当前节点
        </Button>
      )}
    </div>
  )
}

function Deliverables({
  instanceId,
  node,
  detail,
}: {
  instanceId: string
  node: WorkflowNodeInstance
  detail: WorkflowNodeDetail
}) {
  const queryClient = useQueryClient()
  const [notes, setNotes] = useState("")
  const [environment, setEnvironment] = useState("production")
  const [result, setResult] = useState("")
  const isActionableHumanNode =
    detail.definition.node_type === "human" &&
    node.status === "waiting_confirmation"
  const submitHumanNode = useMutation({
    mutationFn: () => {
      const output =
        node.node_key === "acceptance"
          ? { accepted: true, notes }
          : { environment, result, notes }
      return workspaceApi.submitWorkflowNode(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        output,
        reason:
          node.node_key === "acceptance" ? "人工验收通过" : "人工记录上线结果",
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["workflow-task", instanceId],
      })
      void queryClient.invalidateQueries({
        queryKey: ["workflow-node", instanceId, node.node_key],
      })
    },
    onError: () => toast.error("人工提交失败，请刷新后核对最新节点修订"),
  })

  return (
    <div className="space-y-5">
      {isActionableHumanNode && (
        <div className="space-y-3 rounded-lg border p-4">
          <p className="font-medium">
            {node.node_key === "acceptance" ? "人工验收" : "人工上线记录"}
          </p>
          {node.node_key === "go_live" && (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="go-live-environment">环境</Label>
                <Input
                  id="go-live-environment"
                  value={environment}
                  onChange={(event) => setEnvironment(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="go-live-result">上线结果</Label>
                <Input
                  id="go-live-result"
                  value={result}
                  onChange={(event) => setResult(event.target.value)}
                  placeholder="版本、变更单或发布结果"
                />
              </div>
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="human-node-notes">说明</Label>
            <textarea
              id="human-node-notes"
              className="min-h-20 w-full rounded-md border bg-transparent px-3 py-2 text-sm"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="填写验收结论或上线说明"
            />
          </div>
          <Button
            disabled={
              submitHumanNode.isPending ||
              (node.node_key === "go_live" &&
                (!environment.trim() || !result.trim()))
            }
            onClick={() => submitHumanNode.mutate()}
          >
            {node.node_key === "acceptance" ? "确认验收通过" : "记录已上线"}
          </Button>
          {node.node_key === "go_live" && (
            <p className="text-xs text-muted-foreground">
              此操作只记录由人员完成的上线结果，不会触发生产部署。
            </p>
          )}
        </div>
      )}
      <section className="space-y-3">
        <h3 className="font-medium">正式产出修订</h3>
        {detail.revisions.length ? (
          detail.revisions.map((revision, index) => (
            <div
              key={String(revision.id || index)}
              className="space-y-2 rounded-lg border p-3"
            >
              <p className="text-sm text-muted-foreground">
                修订 {String(revision.revision || index + 1)}
              </p>
              <JsonPreview value={revision.output} />
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            当前节点尚无正式产出。
          </p>
        )}
      </section>
      <Separator />
      <section className="space-y-3">
        <h3 className="font-medium">产物引用</h3>
        {detail.artifacts.length ? (
          detail.artifacts.map((artifact, index) => (
            <div
              key={String(artifact.id || index)}
              className="rounded-lg border p-3"
            >
              <JsonPreview value={artifact} />
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            当前节点尚无独立产物引用。
          </p>
        )}
      </section>
    </div>
  )
}

function AgentChat({
  instanceId,
  node,
  detail,
}: {
  instanceId: string
  node: WorkflowNodeInstance
  detail: WorkflowNodeDetail
}) {
  const queryClient = useQueryClient()
  const [message, setMessage] = useState("")
  const sendMessage = useMutation({
    mutationFn: () =>
      workspaceApi.sendWorkflowNodeMessage(
        instanceId,
        node.node_key,
        node.expected_revision,
        message,
      ),
    onSuccess: () => {
      setMessage("")
      void queryClient.invalidateQueries({
        queryKey: ["workflow-node", instanceId, node.node_key],
      })
    },
    onError: () => toast.error("消息发送失败，Agent 会话可能尚未启动"),
  })
  if (!detail.agent) {
    return (
      <p className="text-sm text-muted-foreground">
        人工节点不提供 Agent 聊天。
      </p>
    )
  }
  return (
    <div className="space-y-4">
      <div className="max-h-[28rem] space-y-3 overflow-auto rounded-lg border p-4">
        {detail.messages.length ? (
          detail.messages.map((item) => (
            <div key={item.id} className="space-y-1 rounded-lg bg-muted p-3">
              <p className="text-xs text-muted-foreground">
                {item.author_type} ·{" "}
                {new Date(item.created_at).toLocaleString()}
              </p>
              <p className="whitespace-pre-wrap text-sm">
                {typeof item.payload.content === "string"
                  ? item.payload.content
                  : JSON.stringify(item.payload, null, 2)}
              </p>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            Agent 会话将在该节点开始执行后创建。
          </p>
        )}
      </div>
      <div className="flex gap-2">
        <Input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="向当前节点 Agent 补充信息"
          disabled={!detail.messages.length}
          onKeyDown={(event) => {
            if (event.key === "Enter" && message.trim()) sendMessage.mutate()
          }}
        />
        <Button
          disabled={
            !detail.messages.length || !message.trim() || sendMessage.isPending
          }
          onClick={() => sendMessage.mutate()}
        >
          发送
        </Button>
      </div>
    </div>
  )
}
