import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { workspaceApi } from "@/api/tenantApi"
import { Card, CardContent } from "@/components/ui/card"
import { resolveWorkflowFrontend } from "@/workflowRegistry"

export const Route = createFileRoute("/_layout/apps/$workflowSlug")({
  component: WorkflowApplicationPage,
})

function WorkflowApplicationPage() {
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
  const Application = registration.Application
  return <Application workflowSlug={workflowSlug} />
}
