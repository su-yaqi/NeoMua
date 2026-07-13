import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { workspaceApi } from "@/api/tenantApi"
import { Card, CardContent } from "@/components/ui/card"
import { resolveWorkflowFrontend } from "@/workflowRegistry"

export const Route = createFileRoute(
  "/_layout/apps_/$workflowSlug/tasks/$instanceId",
)({ component: WorkflowTaskPage })

function WorkflowTaskPage() {
  const { instanceId, workflowSlug } = Route.useParams()
  const task = useQuery({
    queryKey: ["workflow-task", instanceId],
    queryFn: () => workspaceApi.getWorkflowInstance(instanceId),
  })
  if (!task.data) return <p>正在解析固定 Workflow 应用版本…</p>
  if (task.data.workflow_slug !== workflowSlug) {
    return (
      <Card>
        <CardContent className="py-8">
          任务不属于当前 Workflow 应用。
        </CardContent>
      </Card>
    )
  }
  const registration = resolveWorkflowFrontend(
    task.data.application.component_key,
  )
  if (!registration) {
    return (
      <Card>
        <CardContent className="py-8">
          固定版本前端组件未随当前构建发布，已阻止使用通用页面替代。
        </CardContent>
      </Card>
    )
  }
  const Task = registration.Task
  return <Task instanceId={instanceId} />
}
