import { createFileRoute, Outlet, useRouterState } from "@tanstack/react-router"
import { pluginsApi } from "@/api/tenantApi"
import { CreatePluginCompleteDialog } from "@/components/Agents/CompleteCapabilityDialogs"
import IdentityManager from "@/components/Agents/IdentityManager"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/plugins")({
  component: Page,
})
function Page() {
  const { user } = useAuth()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  if (pathname !== "/system/plugins") return <Outlet />
  const namespace = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespace,
  )?.role
  return (
    <IdentityManager
      title="Plugin（能力包）"
      description="组合精确 Skill/MCP 版本与只可收紧的 Tool 策略，不加载可执行代码。"
      queryKey="plugins"
      api={pluginsApi}
      canManage={Boolean(user?.is_superuser || role === "admin")}
      createLabel="新建 Plugin"
      identifierLabel="Plugin 标识"
      createAction={<CreatePluginCompleteDialog />}
      onSelect={(item) => window.location.assign(`/system/plugins/${item.id}`)}
    />
  )
}
