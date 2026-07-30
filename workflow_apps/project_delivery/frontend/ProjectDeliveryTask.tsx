import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { type WorkflowNodeInstance, workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"

export function ProjectDeliveryTask({ instanceId }: { instanceId: string }) {
  const queryClient = useQueryClient()
  const task = useQuery({
    queryKey: ["workflow-task", instanceId],
    queryFn: () => workspaceApi.getWorkflowInstance(instanceId),
    refetchInterval: 3000,
  })
  if (!task.data) return <p>正在加载项目交付任务…</p>
  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">{task.data.title}</h1>
          <Badge>{task.data.status}</Badge>
        </div>
        <p className="text-muted-foreground">
          固定 Package {task.data.package_digest} · 版本{" "}
          {task.data.template_version_id}
        </p>
      </div>
      <div className="space-y-4">
        {task.data.nodes.map((node) => (
          <ProjectDeliveryNode
            key={node.id}
            node={node}
            instanceId={instanceId}
            readOnly={
              task.data?.status === "completed" ||
              task.data?.status === "cancelled"
            }
            refresh={() =>
              queryClient.invalidateQueries({
                queryKey: ["workflow-task", instanceId],
              })
            }
          />
        ))}
      </div>
    </div>
  )
}

function ProjectDeliveryNode({
  node,
  instanceId,
  readOnly,
  refresh,
}: {
  node: WorkflowNodeInstance
  instanceId: string
  readOnly: boolean
  refresh: () => void
}) {
  const [goals, setGoals] = useState("")
  const [criteria, setCriteria] = useState("")
  const submit = useMutation({
    mutationFn: () =>
      workspaceApi.submitWorkflowNode(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        output: {
          goals: goals.split("\n").filter(Boolean),
          acceptance_criteria: criteria.split("\n").filter(Boolean),
        },
        reason: "项目成员提交",
      }),
    onSuccess: refresh,
    onError: () => toast.error("提交失败；可能存在并发修订，请刷新比较"),
  })
  const confirm = useMutation({
    mutationFn: (decision: "accept" | "reject") =>
      workspaceApi.confirmWorkflowNode(instanceId, node.node_key, {
        expected_revision: node.expected_revision,
        decision,
      }),
    onSuccess: refresh,
    onError: () => toast.error("确认失败；候选结果可能已更新"),
  })
  const isClarification = node.node_key === "clarify"
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
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
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          修订 {node.expected_revision} · Runtime {node.resolved_runtime_id}
        </p>
        {!readOnly &&
          isClarification &&
          node.status === "waiting_confirmation" &&
          node.expected_revision === 0 && (
            <div className="grid gap-2">
              <Input
                placeholder="目标，每行一项"
                value={goals}
                onChange={(event) => setGoals(event.target.value)}
              />
              <Input
                placeholder="验收标准，每行一项"
                value={criteria}
                onChange={(event) => setCriteria(event.target.value)}
              />
              <Button
                disabled={!goals || !criteria}
                onClick={() => submit.mutate()}
              >
                提交澄清结果
              </Button>
            </div>
          )}
        {!readOnly &&
          !isClarification &&
          node.status === "waiting_confirmation" &&
          node.expected_revision > 0 && (
            <div className="flex gap-2">
              <Button onClick={() => confirm.mutate("accept")}>
                接受交付摘要
              </Button>
              <Button
                variant="outline"
                onClick={() => confirm.mutate("reject")}
              >
                拒绝并重跑
              </Button>
            </div>
          )}
        {readOnly && (
          <p className="text-sm text-muted-foreground">
            任务已结束，节点历史只读。
          </p>
        )}
      </CardContent>
    </Card>
  )
}
