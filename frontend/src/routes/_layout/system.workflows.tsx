import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const Route = createFileRoute("/_layout/system/workflows")({
  component: WorkflowManagementPage,
})

function WorkflowManagementPage() {
  const queryClient = useQueryClient()
  const templates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: workspaceApi.listWorkflowTemplates,
  })
  const update = useMutation({
    mutationFn: ({
      templateId,
      versionId,
      enabled,
    }: {
      templateId: string
      versionId: string
      enabled: boolean
    }) =>
      workspaceApi.updateWorkflowEnablement(templateId, {
        template_version_id: versionId,
        enabled,
        is_default: enabled,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["workflow-templates"] }),
    onError: () => toast.error("启停失败；blocked 版本不能启用"),
  })
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Workflow 模板</h1>
        <p className="text-muted-foreground">
          这里只管理随构建部署且摘要匹配的不可变版本；不接受在线代码或远程
          Bundle。
        </p>
      </div>
      {templates.data?.data.map((template) => (
        <Card key={template.id}>
          <CardHeader>
            <div className="flex justify-between">
              <CardTitle>{template.name}</CardTitle>
              <Badge>{template.scope_type}</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {template.description}
            </p>
            {template.versions.map(
              ({ version, enablement, execution_configuration }) => (
                <div
                  key={version.id}
                  className="flex items-center justify-between rounded-md border p-3"
                >
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{version.version}</p>
                      <Badge variant="outline">
                        {version.manifest.project_mode === "optional"
                          ? "项目可选"
                          : version.manifest.project_mode === "none"
                            ? "无需项目"
                            : "必须关联项目"}
                      </Badge>
                    </div>
                    <p className="font-mono text-xs text-muted-foreground">
                      {version.package_digest}
                    </p>
                    <Badge
                      className="mt-2"
                      variant={
                        execution_configuration ? "secondary" : "destructive"
                      }
                    >
                      {execution_configuration
                        ? `执行配置 r${execution_configuration.revision}`
                        : "执行配置未完成"}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" asChild>
                      <Link
                        to="/system/workflows/$templateId/versions/$versionId/configuration"
                        params={{
                          templateId: template.id,
                          versionId: version.id,
                        }}
                      >
                        配置执行节点
                      </Link>
                    </Button>
                    <Button
                      variant={enablement.enabled ? "outline" : "default"}
                      onClick={() =>
                        update.mutate({
                          templateId: template.id,
                          versionId: version.id,
                          enabled: !enablement.enabled,
                        })
                      }
                    >
                      {enablement.enabled ? "停用" : "启用并设为默认"}
                    </Button>
                  </div>
                </div>
              ),
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
