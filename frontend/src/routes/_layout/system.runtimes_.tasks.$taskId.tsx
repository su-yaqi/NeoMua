import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { type RuntimeEvent, tenantApi } from "@/api/tenantApi"
import TaskEventTimeline from "@/components/Runtimes/TaskEventTimeline"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const Route = createFileRoute("/_layout/system/runtimes_/tasks/$taskId")(
  {
    component: RuntimeTaskPage,
  },
)

function RuntimeTaskPage() {
  const { taskId } = Route.useParams()
  const [events, setEvents] = useState<RuntimeEvent[]>([])
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const task = useQuery({
    queryKey: ["runtime-task", taskId],
    queryFn: () => tenantApi.readRuntimeTask(taskId),
    refetchInterval: 3000,
  })
  const approvals = useQuery({
    queryKey: ["task-approvals", taskId],
    queryFn: () => tenantApi.readTaskApprovals(taskId),
    refetchInterval: 2000,
  })
  const decide = useMutation({
    mutationFn: ({
      id,
      decision,
      digest,
    }: {
      id: string
      decision: "approve" | "deny"
      digest: string
    }) => tenantApi.decideToolApproval(id, decision, digest),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["task-approvals", taskId] }),
  })
  useEffect(() => {
    const controller = new AbortController()
    tenantApi
      .streamRuntimeEvents(
        taskId,
        (event) =>
          setEvents((current) =>
            current.some((item) => item.sequence === event.sequence)
              ? current
              : [...current, event],
          ),
        controller.signal,
      )
      .then(() =>
        queryClient.invalidateQueries({ queryKey: ["runtime-task", taskId] }),
      )
      .catch(() => undefined)
    return () => controller.abort()
  }, [taskId, queryClient])
  const cancel = useMutation({
    mutationFn: () => tenantApi.cancelRuntimeTask(taskId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["runtime-task", taskId] }),
  })
  const retry = useMutation({
    mutationFn: () => tenantApi.retryRuntimeTask(taskId, crypto.randomUUID()),
    onSuccess: (next) =>
      navigate({
        to: "/system/runtimes/tasks/$taskId",
        params: { taskId: next.id },
      }),
  })
  const retryable = ["failed", "interrupted", "rejected", "cancelled"].includes(
    task.data?.status ?? "",
  )
  const cancellable = [
    "queued",
    "dispatched",
    "running",
    "awaiting_approval",
  ].includes(task.data?.status ?? "")
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agent 任务</h1>
          <p className="font-mono text-sm text-muted-foreground">{taskId}</p>
        </div>
        <div className="flex gap-2">
          {cancellable ? (
            <Button variant="outline" onClick={() => cancel.mutate()}>
              取消任务
            </Button>
          ) : null}
          {retryable ? (
            <Button onClick={() => retry.mutate()}>重新执行</Button>
          ) : null}
        </div>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>状态：{task.data?.status ?? "加载中"}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-3">{task.data?.prompt}</p>
          <details>
            <summary>不可变运行时快照</summary>
            <pre className="mt-2 overflow-auto text-xs">
              {JSON.stringify(task.data?.snapshot, null, 2)}
            </pre>
          </details>
        </CardContent>
      </Card>
      {task.data?.skill_usage?.length ? (
        <Card>
          <CardHeader>
            <CardTitle>实际 Skill 使用记录</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {task.data.skill_usage.map((usage) => (
              <button
                type="button"
                key={String(usage.skill_id)}
                className="grid w-full gap-2 rounded border p-3 text-left text-sm md:grid-cols-4"
                onClick={() =>
                  window.location.assign(
                    `/system/skills/${String(usage.skill_id)}`,
                  )
                }
              >
                <span>{String(usage.skill_name ?? usage.skill_slug)}</span>
                <span>v{String(usage.version)}</span>
                <code>{String(usage.content_sha256).slice(0, 16)}</code>
                <span>generation {String(usage.runtime_generation)}</span>
              </button>
            ))}
          </CardContent>
        </Card>
      ) : null}
      {task.data?.model_call_usage?.length ? (
        <Card>
          <CardHeader>
            <CardTitle>逐次模型调用记录</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {task.data.model_call_usage.map((usage) => (
              <div
                key={String(usage.id)}
                className="grid gap-2 rounded border p-3 text-sm md:grid-cols-4"
              >
                <span>调用 #{String(usage.call_sequence)}</span>
                <span>事件 #{String(usage.event_sequence)}</span>
                <span>状态：{String(usage.status)}</span>
                <code
                  className="truncate"
                  title={String(usage.runtime_model_binding_id)}
                >
                  {String(usage.runtime_model_binding_id)}
                </code>
                <pre className="overflow-auto rounded bg-muted p-2 text-xs md:col-span-4">
                  {JSON.stringify(usage.usage, null, 2)}
                </pre>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}
      {((approvals.data?.data ?? []) as Array<Record<string, unknown>>).map(
        (approval) => (
          <Card key={String(approval.id)}>
            <CardHeader>
              <CardTitle>
                Tool 审批：{String(approval.tool_qualified_name)}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p>状态：{String(approval.status)}</p>
              <pre className="overflow-auto rounded bg-muted p-2 text-xs">
                {JSON.stringify(approval.redacted_args, null, 2)}
              </pre>
              <code className="text-xs">{String(approval.args_digest)}</code>
              {approval.status === "pending" && (
                <div className="flex gap-2">
                  <Button
                    onClick={() =>
                      decide.mutate({
                        id: String(approval.id),
                        decision: "approve",
                        digest: String(approval.args_digest),
                      })
                    }
                  >
                    批准一次
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() =>
                      decide.mutate({
                        id: String(approval.id),
                        decision: "deny",
                        digest: String(approval.args_digest),
                      })
                    }
                  >
                    拒绝
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        ),
      )}
      <TaskEventTimeline events={events} />
    </div>
  )
}
