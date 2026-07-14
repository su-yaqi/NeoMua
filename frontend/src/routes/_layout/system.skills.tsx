import { createFileRoute, Outlet, useRouterState } from "@tanstack/react-router"
import { skillsApi } from "@/api/tenantApi"
import { CreateSkillCompleteDialog } from "@/components/Agents/CompleteCapabilityDialogs"
import IdentityManager from "@/components/Agents/IdentityManager"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/skills")({
  component: Page,
})
function Page() {
  const { user } = useAuth()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  if (pathname !== "/system/skills") return <Outlet />
  const namespace = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespace,
  )?.role
  return (
    <IdentityManager
      title="Skill 管理"
      description="声明式 Skill 身份与不可变版本；脚本和可执行内容会被拒绝。"
      queryKey="skills"
      api={skillsApi}
      canManage={Boolean(user?.is_superuser || role === "admin")}
      createLabel="新建 Skill"
      identifierLabel="Skill 标识"
      createAction={<CreateSkillCompleteDialog />}
      onSelect={(item) => window.location.assign(`/system/skills/${item.id}`)}
    />
  )
}
