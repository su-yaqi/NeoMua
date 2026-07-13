import { createFileRoute } from "@tanstack/react-router"
import ArtifactPanel from "@/components/Runtimes/ArtifactPanel"
import NodeTable from "@/components/Runtimes/NodeTable"
import PlatformRuntimeCard from "@/components/Runtimes/PlatformRuntimeCard"
import RuntimeAgentMatrix from "@/components/Runtimes/RuntimeAgentMatrix"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/runtimes")({
  component: RuntimeManagementPage,
})

function RuntimeManagementPage() {
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
    (item) => item.namespace_id === namespaceId,
  )?.role
  const visible = Boolean(
    user?.is_superuser || role === "admin" || role === "developer",
  )
  if (user && !visible) return null
  const canManage = Boolean(user?.is_superuser || role === "admin")
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">运行时管理</h1>
        <p className="text-muted-foreground">
          配置平台运行时，管理节点连接并执行 Agent 任务。
        </p>
      </div>
      <PlatformRuntimeCard canManage={canManage} />
      <NodeTable canManage={canManage} />
      <RuntimeAgentMatrix />
      <ArtifactPanel canManage={canManage} />
    </div>
  )
}
