import {
  Bot,
  Briefcase,
  Building2,
  Cpu,
  FolderKanban,
  Home,
  MessageSquare,
  Sparkles,
  Users,
  Workflow,
} from "lucide-react"

import { SidebarAppearance } from "@/components/Common/Appearance"
import { Logo } from "@/components/Common/Logo"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { type Item, Main } from "./Main"
import { User } from "./User"

const baseItems: Item[] = [
  { icon: Home, title: "Dashboard", path: "/" },
  { icon: Briefcase, title: "Items", path: "/items" },
  { icon: MessageSquare, title: "AI 工作台", path: "/workspace" },
  { icon: Workflow, title: "Workflow", path: "/workflows" },
  { icon: FolderKanban, title: "项目", path: "/projects" },
]

export function AppSidebar() {
  const { user: currentUser } = useAuth()
  const selectedNamespaceId = localStorage.getItem("selected_namespace_id")
  const canManageSelectedNamespace =
    Boolean(currentUser) && Boolean(selectedNamespaceId)
  const selectedRole = currentUser?.namespace_roles?.find(
    (assignment) => assignment.namespace_id === selectedNamespaceId,
  )?.role
  const canUseRuntimes =
    currentUser?.is_superuser ||
    selectedRole === "admin" ||
    selectedRole === "developer"

  const items: Item[] = [...baseItems]

  if (currentUser) {
    if (canManageSelectedNamespace && selectedNamespaceId) {
      if (canUseRuntimes) {
        items.push({ icon: Cpu, title: "运行时管理", path: "/system/runtimes" })
        items.push({
          icon: Bot,
          title: "Agent 管理",
          children: [
            { title: "Agent", path: "/system/agents" },
            { title: "Harness 配置", path: "/system/harnesses" },
            { title: "Skills", path: "/system/skills" },
            { title: "Tools", path: "/system/tools" },
            { title: "MCP Servers", path: "/system/mcp-servers" },
            { title: "Plugins", path: "/system/plugins" },
          ],
        })
        items.push({
          icon: Workflow,
          title: "Workflow 管理",
          children: [
            { title: "流程模板", path: "/system/workflows" },
            { title: "Spec 标准", path: "/system/spec-standards" },
          ],
        })
      }
      items.push({
        icon: Users,
        title: "空间成员管理",
        path: `/system/namespaces/${selectedNamespaceId}/members`,
      })
      items.push({
        icon: Sparkles,
        title: "大模型接入配置",
        path: "/system/llm-providers",
      })
    }

    items.push({
      icon: Building2,
      title: "空间管理",
      path: "/system/namespaces",
    })
  }

  if (currentUser?.is_superuser) {
    items.push({
      icon: Users,
      title: "用户管理",
      path: "/admin",
    })
  }

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-6 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        <Logo variant="responsive" />
      </SidebarHeader>
      <SidebarContent>
        <Main items={items} />
      </SidebarContent>
      <SidebarFooter>
        <SidebarAppearance />
        <User user={currentUser} />
      </SidebarFooter>
    </Sidebar>
  )
}

export default AppSidebar
