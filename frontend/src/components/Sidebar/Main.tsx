import { Link as RouterLink, useRouterState } from "@tanstack/react-router"
import type { LucideIcon } from "lucide-react"

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"

export type Item = {
  icon: LucideIcon
  title: string
  path?: string
  children?: Array<{ title: string; path: string }>
}

interface MainProps {
  items: Item[]
}

export function Main({ items }: MainProps) {
  const { isMobile, setOpenMobile } = useSidebar()
  const router = useRouterState()
  const currentPath = router.location.pathname

  const handleMenuClick = () => {
    if (isMobile) {
      setOpenMobile(false)
    }
  }

  return (
    <SidebarGroup>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            const isActive = item.path
              ? currentPath === item.path
              : item.children?.some((child) => currentPath === child.path)

            return (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton
                  tooltip={item.title}
                  isActive={isActive}
                  asChild
                >
                  <RouterLink
                    to={item.path || item.children?.[0]?.path || "/"}
                    params={(prev) => prev}
                    search={(prev) => prev}
                    onClick={handleMenuClick}
                  >
                    <item.icon />
                    <span>{item.title}</span>
                  </RouterLink>
                </SidebarMenuButton>
                {item.children?.length ? (
                  <div className="ml-9 mt-1 space-y-1">
                    {item.children.map((child) => (
                      <RouterLink
                        key={child.path}
                        to={child.path}
                        params={(prev) => prev}
                        search={(prev) => prev}
                        onClick={handleMenuClick}
                        className={`block text-sm ${
                          currentPath === child.path
                            ? "text-foreground font-medium"
                            : "text-muted-foreground"
                        }`}
                      >
                        {child.title}
                      </RouterLink>
                    ))}
                  </div>
                ) : null}
              </SidebarMenuItem>
            )
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
