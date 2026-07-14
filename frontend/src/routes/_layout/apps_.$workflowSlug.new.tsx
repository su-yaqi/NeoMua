import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { workspaceApi } from "@/api/tenantApi"
import { Card, CardContent } from "@/components/ui/card"
import { resolveWorkflowFrontend } from "@/workflowRegistry"

export const Route = createFileRoute("/_layout/apps_/$workflowSlug/new")({
  component: WorkflowCreatePage,
})

function WorkflowCreatePage() {
  const { workflowSlug } = Route.useParams()
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
  if (!templates.data) return <p>正在解析 Workflow 新建组件…</p>
  const componentKey = selectedVersion?.application?.component_key
  const registration = componentKey
    ? resolveWorkflowFrontend(componentKey)
    : null
  if (!workflow || !selectedVersion || !registration) {
    return (
      <Card>
        <CardContent className="py-8">
          当前应用默认版本的新建组件不可用，已阻止使用通用表单替代。
        </CardContent>
      </Card>
    )
  }
  const Create = registration.Create
  return (
    <Create
      workflowSlug={workflowSlug}
      templateId={workflow.id}
      templateVersionId={selectedVersion.version.id}
    />
  )
}
