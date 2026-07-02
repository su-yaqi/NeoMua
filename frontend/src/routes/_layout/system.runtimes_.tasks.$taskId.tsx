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
  const cancellable = ["queued", "dispatched", "running"].includes(
    task.data?.status ?? "",
  )
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
      <TaskEventTimeline events={events} />
    </div>
  )
}
