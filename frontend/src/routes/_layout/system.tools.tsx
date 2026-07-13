import { createFileRoute } from "@tanstack/react-router"
import ToolPolicyManager from "@/components/Agents/ToolPolicyManager"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/tools")({
  component: Page,
})
function Page() {
  const { user } = useAuth()
  const namespace = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespace,
  )?.role
  return (
    <ToolPolicyManager
      canManage={Boolean(user?.is_superuser || role === "admin")}
    />
  )
}
