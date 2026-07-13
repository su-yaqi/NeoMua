import { createFileRoute, Outlet, useRouterState } from "@tanstack/react-router"
import { mcpServersApi } from "@/api/tenantApi"
import IdentityManager from "@/components/Agents/IdentityManager"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/mcp-servers")({
  component: Page,
})
function Page() {
  const { user } = useAuth()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  if (pathname !== "/system/mcp-servers") return <Outlet />
  const namespace = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespace,
  )?.role
  return (
    <IdentityManager
      title="MCP Servers"
      description="管理不可变 Revision、目标凭据句柄、校验状态和 Tool 快照。"
      queryKey="mcp-servers"
      api={mcpServersApi}
      canManage={Boolean(user?.is_superuser || role === "admin")}
      onSelect={(item) =>
        window.location.assign(`/system/mcp-servers/${item.id}`)
      }
    />
  )
}
