import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useMemo, useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
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

export const Route = createFileRoute("/_layout/apps/$workflowSlug")({
  component: WorkflowApplicationPage,
})

function WorkflowApplicationPage() {
  const { workflowSlug } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [projectId, setProjectId] = useState("")
  const [title, setTitle] = useState("")
  const [request, setRequest] = useState("")
  const templates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: workspaceApi.listWorkflowTemplates,
  })
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => workspaceApi.listProjects(),
  })
  const workflow = useMemo(
    () =>
      templates.data?.data.find(
        (item) => (item.application?.route_slug || item.slug) === workflowSlug,
      ),
    [templates.data, workflowSlug],
  )
  const enabledVersion =
    workflow?.versions.find(
      (item) => item.enablement.enabled && item.enablement.is_default,
    ) || workflow?.versions.find((item) => item.enablement.enabled)
  const tasks = useQuery({
    queryKey: ["workflow-app-tasks", workflowSlug, projectId],
    queryFn: () => workspaceApi.listWorkflowInstances(projectId),
    enabled: Boolean(projectId),
  })
  const createTask = useMutation({
    mutationFn: () =>
      workspaceApi.createWorkflowInstance(projectId, {
        title,
        template_version_id: enabledVersion?.version.id,
        input: { request },
      }),
    onSuccess: (task) => {
      queryClient.invalidateQueries({ queryKey: ["workflow-app-tasks"] })
      navigate({
        to: "/apps/$workflowSlug/tasks/$instanceId",
        params: { workflowSlug, instanceId: task.id },
      })
    },
    onError: () =>
      toast.error("任务预检失败；请检查项目仓库、Runtime、Agent 与 Schema"),
  })
  if (!workflow)
    return (
      <Card>
        <CardContent className="py-8">
          Workflow 应用不存在或尚未部署。
        </CardContent>
      </Card>
    )
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{workflow.name}</h1>
        <p className="text-muted-foreground">{workflow.description}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>创建项目任务</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
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
          <Input
            placeholder="任务标题"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <Input
            className="md:col-span-2"
            placeholder="业务请求"
            value={request}
            onChange={(event) => setRequest(event.target.value)}
          />
          <div className="flex items-center justify-between md:col-span-2">
            <p className="text-sm text-muted-foreground">
              固定版本：{enabledVersion?.version.version || "无已启用版本"}
            </p>
            <Button
              disabled={!projectId || !title || !request || !enabledVersion}
              onClick={() => createTask.mutate()}
            >
              预检并创建
            </Button>
          </div>
        </CardContent>
      </Card>
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">项目任务</h2>
        {tasks.data?.data
          .filter(
            (task) => task.template_version_id === enabledVersion?.version.id,
          )
          .map((task) => (
            <Link
              key={task.id}
              to="/apps/$workflowSlug/tasks/$instanceId"
              params={{ workflowSlug, instanceId: task.id }}
            >
              <Card className="hover:border-primary">
                <CardContent className="flex justify-between py-5">
                  <div>
                    <p className="font-medium">{task.title}</p>
                    <p className="text-sm text-muted-foreground">
                      {task.package_digest.slice(0, 12)}
                    </p>
                  </div>
                  <Badge>{task.status}</Badge>
                </CardContent>
              </Card>
            </Link>
          ))}
      </div>
    </div>
  )
}
