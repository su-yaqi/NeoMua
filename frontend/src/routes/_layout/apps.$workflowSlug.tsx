import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { resolveWorkflowFrontend } from "@/workflowRegistry"

export const Route = createFileRoute("/_layout/apps/$workflowSlug")({
  component: WorkflowApplicationPage,
})

function WorkflowApplicationPage() {
  const { workflowSlug } = Route.useParams()
  const navigate = useNavigate()
  const templates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: workspaceApi.listWorkflowTemplates,
  })
  const workflow = templates.data?.data.find(
    (item) => (item.application?.route_slug || item.slug) === workflowSlug,
  )
  const selectedVersion =
    workflow?.versions.find(
      (item) => item.enablement.enabled && item.enablement.is_default,
    ) || workflow?.versions.find((item) => item.enablement.enabled)
  const instances = useQuery({
    queryKey: ["workflow-template-instances", workflow?.id],
    queryFn: () =>
      workspaceApi.listWorkflowTemplateInstances(workflow?.id || ""),
    enabled: Boolean(workflow?.id),
  })
  const available =
    templates.data?.data.filter((item) =>
      item.versions.some(
        (version) =>
          version.enablement.enabled &&
          version.application &&
          resolveWorkflowFrontend(version.application.component_key),
      ),
    ) || []
  if (!templates.data) return <p>正在解析 Workflow 应用版本…</p>
  const componentKey = selectedVersion?.application?.component_key
  const registration = componentKey
    ? resolveWorkflowFrontend(componentKey)
    : null
  if (!workflow || !registration) {
    return (
      <Card>
        <CardContent className="py-8">
          Workflow 应用未启用，或固定版本组件未随当前构建发布。
        </CardContent>
      </Card>
    )
  }
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <Select
            value={workflowSlug}
            onValueChange={(nextSlug) =>
              navigate({
                to: "/apps/$workflowSlug",
                params: { workflowSlug: nextSlug },
              })
            }
          >
            <SelectTrigger className="min-w-64 text-lg font-semibold">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {available.map((item) => (
                <SelectItem
                  key={item.id}
                  value={item.application?.route_slug || item.slug}
                >
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-sm text-muted-foreground">
            {workflow.description || "暂无应用说明"}
          </p>
        </div>
        <Button asChild>
          <Link to="/apps/$workflowSlug/new" params={{ workflowSlug }}>
            <Plus /> 新建流程实例
          </Link>
        </Button>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>流程实例</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {instances.isPending && (
            <p className="text-sm text-muted-foreground">正在加载流程实例…</p>
          )}
          {instances.isError && (
            <p className="text-sm text-destructive">流程实例加载失败。</p>
          )}
          {!instances.isPending && !instances.data?.data.length && (
            <div className="py-10 text-center text-sm text-muted-foreground">
              当前应用还没有流程实例。
            </div>
          )}
          {instances.data?.data.map((instance) => (
            <Link
              key={instance.id}
              to="/apps/$workflowSlug/tasks/$instanceId"
              params={{ workflowSlug, instanceId: instance.id }}
              className="block rounded-lg border p-4 transition-colors hover:border-primary"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="font-medium">{instance.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {instance.context_mode === "project"
                      ? `项目流程 · ${instance.project_id}`
                      : "独立流程"}{" "}
                    · 更新于 {new Date(instance.updated_at).toLocaleString()}
                  </p>
                </div>
                <Badge>{instance.status}</Badge>
              </div>
            </Link>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}
