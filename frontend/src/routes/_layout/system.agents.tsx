import { createFileRoute } from "@tanstack/react-router"
import AgentList from "@/components/Agents/AgentList"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/agents")({
  component: AgentsPage,
})

function AgentsPage() {
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
  const visible = Boolean(
    user?.is_superuser || role === "admin" || role === "developer"
  )
  if (user && !visible) return null
  const canManage = Boolean(user?.is_superuser || role === "admin")
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Agent 管理</h1>
        <p className="text-muted-foreground">
          管理空间级 Agent 定义、草稿与 Claude Harness 配置。
        </p>
      </div>
      <AgentList canManage={canManage} />
    </div>
  )
}
