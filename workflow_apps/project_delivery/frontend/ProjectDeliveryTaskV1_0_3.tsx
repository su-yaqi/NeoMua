import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
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

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function ProjectDeliveryTaskV1_0_3({
  instanceId,
}: {
  instanceId: string
}) {
  const queryClient = useQueryClient()
  const lastEventId = useRef(0)
  const [streamState, setStreamState] = useState("connecting")
  const [attachment, setAttachment] = useState<File | null>(null)
  const task = useQuery({
    queryKey: ["workflow-task", instanceId],
    queryFn: () => workspaceApi.getWorkflowInstance(instanceId),
  })
  const events = useQuery({
    queryKey: ["workflow-events", instanceId],
    queryFn: () => workspaceApi.listWorkflowEvents(instanceId),
  })
  const attachments = useQuery({
    queryKey: ["workflow-attachments", instanceId],
    queryFn: () => workspaceApi.listWorkflowAttachments(instanceId),
  })
  useEffect(() => {
    if (!task.data || ["completed", "cancelled"].includes(task.data.status)) {
      setStreamState("closed")
      return
    }
    const controller = new AbortController()
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const connect = async () => {
      setStreamState(lastEventId.current ? "reconnecting" : "connecting")
      try {
        setStreamState("connected")
        await workspaceApi.streamWorkflowEvents(
          instanceId,
          lastEventId.current,
          (event) => {
            lastEventId.current = Math.max(lastEventId.current, event.sequence)
            void queryClient.invalidateQueries({
              queryKey: ["workflow-task", instanceId],
            })
            void queryClient.invalidateQueries({
              queryKey: ["workflow-node", instanceId],
            })
            void queryClient.invalidateQueries({
              queryKey: ["workflow-events", instanceId],
            })
          },
          controller.signal,
        )
      } catch (error) {
        if (!controller.signal.aborted) {
          setStreamState("reconnecting")
          console.error("Workflow event stream disconnected", error)
        }
      }
      if (!controller.signal.aborted) {
        retryTimer = setTimeout(() => void connect(), 1000)
      }
    }
    void connect()
    return () => {
      controller.abort()
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [instanceId, queryClient, task.data?.status])
  const cancel = useMutation({
    mutationFn: () => workspaceApi.cancelWorkflowInstance(instanceId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["workflow-task", instanceId],
      }),
    onError: () => toast.error("取消失败；任务状态可能已经变化"),
  })
  const uploadAttachment = useMutation({
    mutationFn: () => {
      if (!attachment) throw new Error("No attachment selected")
      return workspaceApi.uploadWorkflowAttachment(instanceId, attachment)
    },
    onSuccess: () => {
      setAttachment(null)
      void queryClient.invalidateQueries({
        queryKey: ["workflow-attachments", instanceId],
      })
    },
    onError: () => toast.error("附件未通过严格文本扫描或任务已只读"),
  })
  if (!task.data) return <p>正在加载固定版本项目交付任务…</p>
  const readOnly = ["completed", "cancelled"].includes(task.data.status)
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{task.data.title}</h1>
            <Badge>{task.data.status}</Badge>
            <Badge variant="outline">事件流 {streamState}</Badge>
          </div>
          <p className="text-muted-foreground">
            固定 Package {task.data.package_digest} · 模板版本{" "}
            {task.data.template_version_id} · 组件{" "}
            {task.data.application.component_key}
          </p>
        </div>
        {!readOnly && (
          <Button
            variant="outline"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            取消任务
          </Button>
        )}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>项目与 Runtime 固定快照</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-2">
          <JsonBlock value={task.data.input} />
          <JsonBlock
            value={{
              runtime_resolution: task.data.runtime_resolution,
              project_context_snapshot: task.data.project_context_snapshot,
            }}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>任务附件</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!readOnly && (
            <div className="flex flex-wrap items-center gap-3">
              <input
                aria-label="Workflow 任务附件"
                accept=".txt,.md,.markdown,.json,text/plain,text/markdown,application/json"
                type="file"
                onChange={(event) =>
                  setAttachment(event.target.files?.item(0) ?? null)
                }
              />
              <Button
                disabled={!attachment || uploadAttachment.isPending}
                onClick={() => uploadAttachment.mutate()}
              >
                上传任务附件
              </Button>
              <span className="text-xs text-muted-foreground">
                仅 UTF-8 TXT/Markdown/JSON，最大 5 MiB；完成后只读。
              </span>
            </div>
          )}
          {attachments.data?.data.length ? (
            attachments.data.data.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap justify-between gap-2 rounded border p-3 text-sm"
              >
                <span>{item.filename}</span>
                <span className="text-muted-foreground">
                  {item.size} bytes · {item.scan_status} · sha256:{" "}
                  {item.content_digest.slice(0, 12)}
                </span>
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">暂无任务附件。</p>
          )}
        </CardContent>
      </Card>
      <div className="space-y-4">
        {task.data.nodes.map((node) => (
          <ProjectDeliveryNodeV1_0_3
            key={node.id}
            node={node}
            instanceId={instanceId}
            readOnly={readOnly}
          />
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>任务审计事件</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {events.data?.data.map((event) => (
            <div key={event.id} className="rounded border p-3 text-sm">
              <div className="flex justify-between">
                <span>
                  #{event.sequence} {event.event_type}
                </span>
                <span className="text-muted-foreground">
                  {event.created_at}
                </span>
              </div>
              <JsonBlock value={event.payload} />
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

function ProjectDeliveryNodeV1_0_3({
  node,
  instanceId,
  readOnly,
}: {
  node: WorkflowNodeInstance
  instanceId: string
  readOnly: boolean
}) {
  const queryClient = useQueryClient()
  const [goals, setGoals] = useState("")
  const [criteria, setCriteria] = useState("")
  const [reason, setReason] = useState("")
  const [message, setMessage] = useState("")
  const [rationale, setRationale] = useState("")
  const detail = useQuery({
    queryKey: ["workflow-node", instanceId, node.node_key],
    queryFn: () => workspaceApi.getWorkflowNode(instanceId, node.node_key),
  })
  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ["workflow-task", instanceId],
    })
    void queryClient.invalidateQueries({
      queryKey: ["workflow-node", instanceId, node.node_key],
    })
  }
  const submit = useMutation({
    mutationFn: () =>
      workspaceApi.submitWorkflowNode(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        output: {
          goals: goals.split("\n").filter(Boolean),
          acceptance_criteria: criteria.split("\n").filter(Boolean),
        },
        reason: reason || "项目成员提交",
      }),
    onSuccess: refresh,
    onError: () => toast.error("提交失败；请刷新并比较最新修订"),
  })
  const confirm = useMutation({
    mutationFn: (decision: "accept" | "reject") =>
      workspaceApi.confirmWorkflowNode(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        decision,
        reason: reason || null,
      }),
    onSuccess: refresh,
    onError: () => toast.error("确认失败；候选结果可能已经更新"),
  })
  const skip = useMutation({
    mutationFn: () =>
      workspaceApi.skipWorkflowNode(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        reason,
      }),
    onSuccess: refresh,
    onError: () => toast.error("跳过失败；模板可能不允许或修订已变化"),
  })
  const retry = useMutation({
    mutationFn: () =>
      workspaceApi.retryWorkflowNode(
        instanceId,
        node.node_key,
        node.expected_revision,
      ),
    onSuccess: refresh,
    onError: () => toast.error("重试被阻断；请检查错误和外部状态证明"),
  })
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
      refresh()
    },
    onError: () => toast.error("消息发送失败；Agent 会话可能尚未开始"),
  })
  const uncertainExecution = detail.data?.executions.find(
    (execution) => execution.status === "needs_manual_resolution",
  )
  const resolveExternalState = useMutation({
    mutationFn: () =>
      workspaceApi.resolveWorkflowExternalState(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        execution_id: uncertainExecution?.id,
        conclusion: "not_started",
        allow_retry: true,
        evidence: { operator_statement: rationale },
        rationale,
      }),
    onSuccess: refresh,
    onError: () => toast.error("外部状态证明未被接受"),
  })
  const definition = detail.data?.definition
  const isClarification = node.node_key === "clarify"
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle>{isClarification ? "需求澄清" : "交付摘要"}</CardTitle>
          <Badge
            variant={
              node.status.includes("failed") || node.status.includes("blocked")
                ? "destructive"
                : "secondary"
            }
          >
            {node.status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          修订 {node.expected_revision} · Runtime {node.resolved_runtime_id} ·
          类型 {definition?.node_type || "加载中"} · 确认{" "}
          {definition?.confirmation_mode}
        </p>
        {!readOnly &&
          isClarification &&
          node.status === "waiting_confirmation" &&
          node.expected_revision === 0 && (
            <div className="grid gap-2">
              <textarea
                aria-label="项目目标"
                className="min-h-24 rounded-md border bg-background p-3 text-sm"
                placeholder="目标，每行一项"
                value={goals}
                onChange={(event) => setGoals(event.target.value)}
              />
              <textarea
                aria-label="验收标准"
                className="min-h-24 rounded-md border bg-background p-3 text-sm"
                placeholder="验收标准，每行一项"
                value={criteria}
                onChange={(event) => setCriteria(event.target.value)}
              />
              <Input
                aria-label="提交原因"
                placeholder="本次提交原因"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
              <Button
                disabled={!goals || !criteria || submit.isPending}
                onClick={() => submit.mutate()}
              >
                提交澄清结果
              </Button>
            </div>
          )}
        {!readOnly &&
          definition?.confirmation_mode === "result" &&
          node.status === "waiting_confirmation" &&
          node.expected_revision > 0 && (
            <div className="space-y-2">
              <Input
                aria-label="确认原因"
                placeholder="接受或拒绝原因（可选）"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
              <div className="flex gap-2">
                <Button onClick={() => confirm.mutate("accept")}>
                  接受候选结果
                </Button>
                <Button
                  variant="outline"
                  onClick={() => confirm.mutate("reject")}
                >
                  拒绝并重跑
                </Button>
              </div>
            </div>
          )}
        {!readOnly && definition?.skippable && (
          <div className="flex gap-2">
            <Input
              aria-label="跳过原因"
              placeholder="跳过原因（必填）"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            <Button
              variant="outline"
              disabled={!reason || skip.isPending}
              onClick={() => skip.mutate()}
            >
              跳过节点
            </Button>
          </div>
        )}
        {!readOnly &&
          ["failed", "blocked", "needs_manual_resolution"].includes(
            node.status,
          ) && (
            <Button
              variant="outline"
              disabled={retry.isPending}
              onClick={() => retry.mutate()}
            >
              显式重试
            </Button>
          )}
        {!readOnly && definition?.node_type === "agent" && (
          <div className="flex gap-2">
            <Input
              aria-label="Agent 节点消息"
              placeholder="向固定 Agent 会话追加消息"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
            />
            <Button
              disabled={!message || sendMessage.isPending}
              onClick={() => sendMessage.mutate()}
            >
              发送节点消息
            </Button>
          </div>
        )}
        {!readOnly && uncertainExecution && (
          <div className="space-y-2 rounded-md border border-destructive p-3">
            <p className="text-sm font-medium">外部副作用状态需要人工举证</p>
            <Input
              aria-label="外部状态证明"
              placeholder="说明已证明未开始执行的证据"
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
            <Button
              disabled={!rationale || resolveExternalState.isPending}
              onClick={() => resolveExternalState.mutate()}
            >
              记录证明并允许重试
            </Button>
          </div>
        )}
        <NodeAudit detail={detail.data} />
      </CardContent>
    </Card>
  )
}

function NodeAudit({ detail }: { detail?: WorkflowNodeDetail }) {
  if (!detail)
    return <p className="text-sm text-muted-foreground">加载节点历史…</p>
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <AuditSection title="追加式修订" value={detail.revisions} />
      <AuditSection title="执行轮次与错误" value={detail.executions} />
      <AuditSection title="Gate 结果" value={detail.gates} />
      <AuditSection title="确认与跳过记录" value={detail.confirmations} />
      <AuditSection title="产物与附件引用" value={detail.artifacts} />
      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Agent 持续会话</h3>
        {detail.messages.length ? (
          detail.messages.map((item) => (
            <div key={item.id} className="rounded border p-2 text-sm">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>{item.author_type}</span>
                <span>{item.status}</span>
              </div>
              <p className="whitespace-pre-wrap">
                {String(item.payload.content || JSON.stringify(item.payload))}
              </p>
              {item.error && <JsonBlock value={item.error} />}
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">无 Agent 会话记录。</p>
        )}
      </div>
    </div>
  )
}

function AuditSection({ title, value }: { title: string; value: unknown[] }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {value.length ? (
        <JsonBlock value={value} />
      ) : (
        <p className="text-sm text-muted-foreground">暂无记录。</p>
      )}
    </div>
  )
}
