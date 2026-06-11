import { Briefcase, Building2, Home, Users } from "lucide-react"

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
]

export function AppSidebar() {
  const { user: currentUser } = useAuth()
  const selectedNamespaceId = localStorage.getItem("selected_namespace_id")
  const canManageSelectedNamespace =
    Boolean(currentUser) && Boolean(selectedNamespaceId)

  const items: Item[] = [...baseItems]

  if (currentUser) {
    if (canManageSelectedNamespace && selectedNamespaceId) {
      items.push({
        icon: Users,
        title: "空间成员管理",
        path: `/system/namespaces/${selectedNamespaceId}/members`,
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
