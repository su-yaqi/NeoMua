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

export function WebPlatformDevelopmentCreateV1_0_1({
  workflowSlug,
  templateVersionId,
}: WorkflowCreateProps) {
  const navigate = useNavigate()
  const [title, setTitle] = useState("")
  const [goal, setGoal] = useState("")
  const createTask = useMutation({
    mutationFn: () =>
      workspaceApi.createVisibleWorkflowInstance({
        title: title.trim(),
        template_version_id: templateVersionId,
        input: { goal: goal.trim(), description: "" },
      }),
    onSuccess: (instance) =>
      navigate({
        to: "/apps/$workflowSlug/tasks/$instanceId",
        params: { workflowSlug, instanceId: instance.id },
      }),
    onError: () =>
      toast.error("创建失败，请先在 Workflow 模板中完成并保存执行配置"),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">新建 Web 平台开发任务</h1>
        <p className="text-muted-foreground">
          项目及各节点使用的运行环境和 Agent 已由流程模板固定。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>任务内容</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="web-development-title">任务名称</Label>
            <Input
              id="web-development-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：开发客户服务门户"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="web-development-goal">要做什么</Label>
            <textarea
              id="web-development-goal"
              className="min-h-32 w-full rounded-md border bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="写清楚业务目标、范围、约束和验收要求"
            />
          </div>
          <div className="flex justify-end">
            <Button
              disabled={!title.trim() || !goal.trim() || createTask.isPending}
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
