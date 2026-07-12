import { createFileRoute } from "@tanstack/react-router"
import AgentEditor from "@/components/Agents/AgentEditor"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/agents/$agentId")({
  component: AgentEditorPage,
})

function AgentEditorPage() {
  const { agentId } = Route.useParams()
  const { user } = useAuth()
  const namespaceId = localStorage.getItem("selected_namespace_id")
  if (!namespaceId)
    return (
      <Card>
        <CardHeader>
          <CardTitle>未选择空间</CardTitle>
        </CardHeader>
        <CardContent>请先选择空间。</CardContent>
      </Card>
    )
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  return <AgentEditor agentId={agentId} canManage={canManage} />
}
