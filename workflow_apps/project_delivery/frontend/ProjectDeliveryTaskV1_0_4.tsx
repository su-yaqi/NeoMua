import { useQuery } from "@tanstack/react-query"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { ProjectDeliveryTaskV1_0_3 } from "./ProjectDeliveryTaskV1_0_3.tsx"

export function ProjectDeliveryTaskV1_0_4({
  instanceId,
}: {
  instanceId: string
}) {
  const task = useQuery({
    queryKey: ["workflow-task", instanceId],
    queryFn: () => workspaceApi.getWorkflowInstance(instanceId),
  })
  return (
    <div className="space-y-4">
      {task.data && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 py-4">
            <Badge variant="outline">
              {task.data.context_mode === "project" ? "项目流程" : "独立流程"}
            </Badge>
            <span className="text-sm text-muted-foreground">
              {task.data.project_id
                ? `关联项目：${task.data.project_id}`
                : "本任务不使用项目配置"}
            </span>
          </CardContent>
        </Card>
      )}
      <ProjectDeliveryTaskV1_0_3 instanceId={instanceId} />
    </div>
  )
}
