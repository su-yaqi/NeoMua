import { useQuery } from "@tanstack/react-query"
import {
  createFileRoute,
  Outlet,
  useLocation,
  useNavigate,
} from "@tanstack/react-router"
import { useEffect } from "react"

import { tenantApi } from "@/api/tenantApi"
import { DataTable } from "@/components/Common/DataTable"
import AddSystemNamespace from "@/components/System/AddSystemNamespace"
import { columns } from "@/components/System/columns"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/namespaces")({
  component: SystemNamespacesPage,
})

function SystemNamespacesPage() {
  const { user, isLoading } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { data } = useQuery({
    queryKey: ["system-namespaces"],
    queryFn: tenantApi.readSystemNamespaces,
    enabled: Boolean(user?.is_superuser),
  })

  useEffect(() => {
    if (isLoading) {
      return
    }
    if (user?.is_superuser) {
      return
    }
    const selectedNamespaceId = localStorage.getItem("selected_namespace_id")
    if (selectedNamespaceId) {
      navigate({
        to: "/system/namespaces/$namespaceId/members",
        params: { namespaceId: selectedNamespaceId },
      })
      return
    }
    navigate({ to: "/" })
  }, [isLoading, navigate, user?.is_superuser])

  if (isLoading) {
    return null
  }

  if (!user?.is_superuser) {
    return null
  }

  if (location.pathname !== "/system/namespaces") {
    return <Outlet />
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">空间治理</h1>
        <p className="text-muted-foreground">
          由 superuser 管理平台空间，并进入空间成员管理。
        </p>
      </div>
      <AddSystemNamespace />
      <DataTable columns={columns} data={data?.data ?? []} />
    </div>
  )
}
