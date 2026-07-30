import { useMutation } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import type { WorkflowCreateProps } from "@/workflowRegistry"

export function ProjectDeliveryCreateV1_0_5({
  workflowSlug,
  templateVersionId,
}: WorkflowCreateProps) {
  const navigate = useNavigate()
  const [title, setTitle] = useState("")
  const [request, setRequest] = useState("")
  const createTask = useMutation({
    mutationFn: () =>
      workspaceApi.createVisibleWorkflowInstance({
        title,
        template_version_id: templateVersionId,
        input: { request },
      }),
    onSuccess: (task) =>
      navigate({
        to: "/apps/$workflowSlug/tasks/$instanceId",
        params: { workflowSlug, instanceId: task.id },
      }),
    onError: () => toast.error("任务预检失败；请检查项目仓库与 Runtime 配置"),
  })
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">新建项目交付协作</h1>
        <p className="text-muted-foreground">
          项目、Runtime、Agent 与模型由当前 Workflow 执行配置统一预检并冻结。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>流程实例配置</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Input
            placeholder="任务标题"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <Input
            placeholder="需要完成的业务目标"
            value={request}
            onChange={(event) => setRequest(event.target.value)}
          />
          <div className="flex justify-end md:col-span-2">
            <Button
              disabled={
                !title || !request || createTask.isPending
              }
              onClick={() => createTask.mutate()}
            >
              预检并创建
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
