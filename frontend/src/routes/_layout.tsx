import { useQuery } from "@tanstack/react-query"
import {
  createFileRoute,
  Outlet,
  redirect,
  useRouterState,
} from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { UsersService } from "@/api/generatedCompat"
import { tenantApi } from "@/api/tenantApi"
import { Footer } from "@/components/Common/Footer"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async () => {
    try {
      await UsersService.readUserMe()
    } catch {
      localStorage.removeItem("selected_namespace_id")
      throw redirect({
        to: "/login",
      })
    }
  },
})

function Layout() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const isWorkspace =
    pathname === "/workspace" || pathname.startsWith("/workspace/")
  const { data: namespacesData } = useQuery({
    queryKey: ["my-namespaces"],
    queryFn: tenantApi.readMyNamespaces,
  })
  const namespaces = namespacesData?.data || []
  const [selectedNamespaceId, setSelectedNamespaceId] = useState(
    () => localStorage.getItem("selected_namespace_id") || "",
  )

  useEffect(() => {
    if (!namespacesData) {
      return
    }

    const storedNamespaceId =
      localStorage.getItem("selected_namespace_id") || ""
    if (namespaces.length === 0) {
      if (storedNamespaceId) {
        localStorage.removeItem("selected_namespace_id")
      }
      if (selectedNamespaceId) {
        setSelectedNamespaceId("")
      }
      return
    }

    const hasStoredNamespace = namespaces.some(
      (namespace) => namespace.id === storedNamespaceId,
    )
    const nextNamespaceId = hasStoredNamespace
      ? storedNamespaceId
      : namespaces[0]?.id || ""

    if (nextNamespaceId !== storedNamespaceId) {
      localStorage.setItem("selected_namespace_id", nextNamespaceId)
    }
    if (nextNamespaceId !== selectedNamespaceId) {
      setSelectedNamespaceId(nextNamespaceId)
    }
  }, [namespaces, namespacesData, selectedNamespaceId])

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset
        className={isWorkspace ? "h-svh min-w-0 overflow-hidden" : undefined}
      >
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1 text-muted-foreground" />
          <div className="ml-auto w-64">
            <Select
              value={selectedNamespaceId}
              onValueChange={(value) => {
                localStorage.setItem("selected_namespace_id", value)
                setSelectedNamespaceId(value)
                window.location.reload()
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择空间" />
              </SelectTrigger>
              <SelectContent>
                {namespaces.map((namespace) => (
                  <SelectItem key={namespace.id} value={namespace.id}>
                    {namespace.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </header>
        <main
          className={
            isWorkspace ? "min-h-0 flex-1 overflow-hidden" : "flex-1 p-6 md:p-8"
          }
        >
          {isWorkspace ? (
            <Outlet />
          ) : (
            <div className="mx-auto max-w-7xl">
              <Outlet />
            </div>
          )}
        </main>
        {!isWorkspace && <Footer />}
      </SidebarInset>
    </SidebarProvider>
  )
}

export default Layout
