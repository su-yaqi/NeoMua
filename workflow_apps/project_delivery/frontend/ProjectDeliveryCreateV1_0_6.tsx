import { useMutation } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { WorkflowCreateProps } from "@/workflowRegistry"

export function ProjectDeliveryCreateV1_0_6({
  workflowSlug,
  templateVersionId,
}: WorkflowCreateProps) {
  const navigate = useNavigate()
  const [title, setTitle] = useState("")
  const [request, setRequest] = useState("")
  const createTask = useMutation({
    mutationFn: () =>
      workspaceApi.createVisibleWorkflowInstance({
        title: title.trim(),
        template_version_id: templateVersionId,
        input: { request: request.trim() },
      }),
    onSuccess: (task) =>
      navigate({
        to: "/apps/$workflowSlug/tasks/$instanceId",
        params: { workflowSlug, instanceId: task.id },
      }),
    onError: () =>
      toast.error("创建失败，请先在 Workflow 模板中完成并保存执行配置"),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">新建项目交付任务</h1>
        <p className="text-muted-foreground">
          项目、运行环境和节点资源由流程模板统一配置，本次只需描述业务任务。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>任务内容</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="project-delivery-title">任务名称</Label>
            <Input
              id="project-delivery-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：完成 0.7 版本项目交付"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="project-delivery-request">要做什么</Label>
            <textarea
              id="project-delivery-request"
              className="min-h-32 w-full rounded-md border bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={request}
              onChange={(event) => setRequest(event.target.value)}
              placeholder="写清楚目标、范围和验收要求"
            />
          </div>
          <div className="flex justify-end">
            <Button
              disabled={
                !title.trim() || !request.trim() || createTask.isPending
              }
              onClick={() => createTask.mutate()}
            >
              {createTask.isPending ? "正在创建…" : "创建任务"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
