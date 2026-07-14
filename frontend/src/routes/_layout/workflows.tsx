import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowRight, Workflow } from "lucide-react"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { resolveWorkflowFrontend } from "@/workflowRegistry"

export const Route = createFileRoute("/_layout/workflows")({
  component: WorkflowDirectoryPage,
})

function WorkflowDirectoryPage() {
  const templates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: workspaceApi.listWorkflowTemplates,
  })
  const available =
    templates.data?.data
      .map((template) => {
        const enabledVersions = template.versions.filter(
          (item) => item.enablement.enabled,
        )
        const selected =
          enabledVersions.find((item) => item.enablement.is_default) ||
          enabledVersions[0]
        const application = selected?.application || template.application
        return {
          template,
          enabledVersions,
          application,
          registered: application
            ? Boolean(resolveWorkflowFrontend(application.component_key))
            : false,
        }
      })
      .filter((item) => item.enabledVersions.length > 0) || []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Workflow className="size-6" /> Workflow
        </h1>
        <p className="text-muted-foreground">
          当前空间已启用的流程应用。模板版本和启停仍在 Workflow 管理中维护。
        </p>
      </div>
      {templates.isError && (
        <Card>
          <CardContent className="py-8 text-center text-destructive">
            Workflow 目录加载失败，请稍后重试。
          </CardContent>
        </Card>
      )}
      {!templates.isPending && !templates.isError && available.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            当前空间暂无已启用的 Workflow 应用。
          </CardContent>
        </Card>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {available.map(
          ({ template, enabledVersions, application, registered }) => {
            const card = (
              <Card
                key={template.id}
                className={
                  registered
                    ? "h-full transition-colors hover:border-primary"
                    : "h-full opacity-70"
                }
              >
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <CardTitle>{template.name}</CardTitle>
                    <Badge variant="outline">{template.scope_type}</Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    {template.description || "暂无说明"}
                  </p>
                  <div className="flex items-center justify-between text-sm">
                    <span>{enabledVersions.length} 个已启用版本</span>
                    {registered ? (
                      <span className="inline-flex items-center gap-1 text-primary">
                        打开应用 <ArrowRight className="size-4" />
                      </span>
                    ) : (
                      <span className="text-destructive">
                        前端组件未随构建发布
                      </span>
                    )}
                  </div>
                </CardContent>
              </Card>
            )
            return registered && application ? (
              <Link
                key={template.id}
                to="/apps/$workflowSlug"
                params={{
                  workflowSlug: application.route_slug || template.slug,
                }}
              >
                {card}
              </Link>
            ) : (
              <div key={template.id}>{card}</div>
            )
          },
        )}
      </div>
    </div>
  )
}
