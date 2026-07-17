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

export function WebPlatformDevelopmentCreate({
  workflowSlug,
  templateVersionId,
}: WorkflowCreateProps) {
  const navigate = useNavigate()
  const [title, setTitle] = useState("")
  const [goal, setGoal] = useState("")
  const [description, setDescription] = useState("")
  const createInstance = useMutation({
    mutationFn: () =>
      workspaceApi.createVisibleWorkflowInstance({
        title,
        template_version_id: templateVersionId,
        input: { goal, description },
      }),
    onSuccess: (instance) =>
      navigate({
        to: "/apps/$workflowSlug/tasks/$instanceId",
        params: { workflowSlug, instanceId: instance.id },
      }),
    onError: () =>
      toast.error("创建前预检未通过，请检查项目、Runtime 与 Agent 发布版本"),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">新建 Web 平台开发流程</h1>
        <p className="text-sm text-muted-foreground">
          项目、Runtime、Agent 与模型由当前 Workflow 执行配置预检并冻结。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>基本信息</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="workflow-title">流程标题</Label>
            <Input
              id="workflow-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：客户门户 0.7 版本开发"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="workflow-goal">研发目标</Label>
            <Input
              id="workflow-goal"
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="描述最终需要交付的结果"
            />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="workflow-description">补充说明</Label>
            <textarea
              id="workflow-description"
              className="min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="范围、限制、验收要求或其他背景"
            />
          </div>
          <div className="flex justify-end md:col-span-2">
            <Button
              disabled={
                !title.trim() || !goal.trim() || createInstance.isPending
              }
              onClick={() => createInstance.mutate()}
            >
              预检并创建流程实例
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
