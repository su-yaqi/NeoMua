import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
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
            {template.versions.map(({ version, enablement }) => (
              <div
                key={version.id}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div>
                  <p className="font-medium">{version.version}</p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {version.package_digest}
                  </p>
                </div>
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
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
