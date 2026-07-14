import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useEffect, useMemo, useState } from "react"
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

export const componentKeyV1_0_4 = "workflow.project_delivery.v1_0_4"

type ProjectMode = "required" | "optional" | "none"
type ContextMode = "project" | "standalone"

function projectModeFromManifest(manifest: Record<string, unknown>): ProjectMode {
  const mode = manifest.project_mode
  if (mode === "required" || mode === "optional" || mode === "none") return mode
  return "required"
}

export function ProjectDeliveryApplicationV1_0_4({
  workflowSlug,
}: {
  workflowSlug: string
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [contextMode, setContextMode] = useState<ContextMode>("project")
  const [projectId, setProjectId] = useState("")
  const [runtimeId, setRuntimeId] = useState("")
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
  const runtimes = useQuery({
    queryKey: ["conversation-runtimes"],
    queryFn: workspaceApi.listConversationRuntimes,
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
  const projectMode = enabledVersion
    ? projectModeFromManifest(enabledVersion.version.manifest)
    : "required"
  const manifestNodes = Array.isArray(enabledVersion?.version.manifest.nodes)
    ? enabledVersion.version.manifest.nodes
    : []
  const standaloneNeedsRuntime = manifestNodes.some((node) => {
    if (!node || typeof node !== "object") return true
    const policy = (node as { runtime_policy?: unknown }).runtime_policy
    return !policy || typeof policy !== "object" || !("runtime_id" in policy)
  })

  useEffect(() => {
    if (projectMode === "none") {
      setContextMode("standalone")
      setProjectId("")
    } else if (projectMode === "required") {
      setContextMode("project")
    }
  }, [projectMode])

  const tasks = useQuery({
    queryKey: ["workflow-app-tasks", workflowSlug, enabledVersion?.version.id],
    queryFn: () =>
      workspaceApi.listVisibleWorkflowInstances(enabledVersion?.version.id),
    enabled: Boolean(enabledVersion),
  })
  const createTask = useMutation({
    mutationFn: () =>
      workspaceApi.createVisibleWorkflowInstance({
        title,
        template_version_id: enabledVersion?.version.id,
        project_id: contextMode === "project" ? projectId : null,
        runtime_id: runtimeId && runtimeId !== "project-default" ? runtimeId : null,
        input: { request },
      }),
    onSuccess: (task) => {
      void queryClient.invalidateQueries({ queryKey: ["workflow-app-tasks"] })
      navigate({
        to: "/apps/$workflowSlug/tasks/$instanceId",
        params: { workflowSlug, instanceId: task.id },
      })
    },
    onError: () =>
      toast.error("任务预检失败；请检查项目模式、项目配置、Runtime 与 Agent"),
  })
  if (!workflow)
    return (
      <Card>
        <CardContent className="py-8">
          Workflow 应用不存在或尚未部署。
        </CardContent>
      </Card>
    )

  const projectRequired = contextMode === "project" && !projectId
  const runtimeRequired =
    contextMode === "standalone" && standaloneNeedsRuntime && !runtimeId
  const canCreate =
    Boolean(title && request && enabledVersion) &&
    !projectRequired &&
    !runtimeRequired

  return (
    <div className="space-y-6">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold">{workflow.name}</h1>
          <Badge variant="outline">
            {projectMode === "required"
              ? "必须关联项目"
              : projectMode === "optional"
                ? "项目可选"
                : "无需项目"}
          </Badge>
        </div>
        <p className="text-muted-foreground">{workflow.description}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>创建流程任务</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          {projectMode === "optional" && (
            <Select
              value={contextMode}
              onValueChange={(value: ContextMode) => {
                setContextMode(value)
                if (value === "standalone") setProjectId("")
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择执行方式" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="project">关联项目</SelectItem>
                <SelectItem value="standalone">独立执行</SelectItem>
              </SelectContent>
            </Select>
          )}
          {contextMode === "project" && (
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
          )}
          <Select value={runtimeId} onValueChange={setRuntimeId}>
            <SelectTrigger>
              <SelectValue
                placeholder={
                  contextMode === "project"
                    ? "使用项目默认 Runtime"
                    : "选择任务 Runtime"
                }
              />
            </SelectTrigger>
            <SelectContent>
              {contextMode === "project" && (
                <SelectItem value="project-default">使用项目默认 Runtime</SelectItem>
              )}
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
            className="md:col-span-2"
            placeholder="需要完成的业务目标"
            value={request}
            onChange={(event) => setRequest(event.target.value)}
          />
          <div className="flex items-center justify-between md:col-span-2">
            <p className="text-sm text-muted-foreground">
              固定版本：{enabledVersion?.version.version || "无已启用版本"} ·{" "}
              {contextMode === "project" ? "冻结项目配置" : "无项目上下文"}
            </p>
            <Button
              disabled={!canCreate || createTask.isPending}
              onClick={() => createTask.mutate()}
            >
              预检并创建
            </Button>
          </div>
        </CardContent>
      </Card>
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">流程任务</h2>
        {tasks.data?.data.map((task) => {
          const project = projects.data?.data.find(
            (item) => item.id === task.project_id,
          )
          return (
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
                      {task.context_mode === "project"
                        ? `项目：${project?.name || task.project_id}`
                        : "独立执行"}{" "}
                      · Package {task.package_digest.slice(0, 12)}
                    </p>
                  </div>
                  <Badge>{task.status}</Badge>
                </CardContent>
              </Card>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
