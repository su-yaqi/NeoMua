import { useMutation, useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { WorkflowCreateProps } from "@/workflowRegistry"

export function ProjectDeliveryCreateV1_0_5({
  workflowSlug,
  templateVersionId,
}: WorkflowCreateProps) {
  const navigate = useNavigate()
  const [projectId, setProjectId] = useState("")
  const [runtimeId, setRuntimeId] = useState("project-default")
  const [title, setTitle] = useState("")
  const [request, setRequest] = useState("")
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => workspaceApi.listProjects(),
  })
  const runtimes = useQuery({
    queryKey: ["conversation-runtimes"],
    queryFn: workspaceApi.listConversationRuntimes,
  })
  const createTask = useMutation({
    mutationFn: () =>
      workspaceApi.createVisibleWorkflowInstance({
        title,
        template_version_id: templateVersionId,
        project_id: projectId,
        runtime_id: runtimeId === "project-default" ? null : runtimeId,
        input: { request },
        agent_bindings: {},
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
          本应用必须关联项目，并在创建时冻结项目与 Runtime 配置。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>流程实例配置</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger>
              <SelectValue placeholder="选择项目" />
            </SelectTrigger>
            <SelectContent>
              {projects.data?.data.map((project) => (
                <SelectItem key={project.id} value={project.id}>
                  {project.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={runtimeId} onValueChange={setRuntimeId}>
            <SelectTrigger>
              <SelectValue placeholder="选择 Runtime" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="project-default">
                使用项目默认 Runtime
              </SelectItem>
              {runtimes.data?.data
                .filter((runtime) => runtime.compatible)
                .map((runtime) => (
                  <SelectItem key={runtime.id} value={runtime.id}>
                    {runtime.runtime_type === "platform" ? "平台" : "节点"} ·{" "}
                    {runtime.model_id}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
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
                !projectId || !title || !request || createTask.isPending
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
