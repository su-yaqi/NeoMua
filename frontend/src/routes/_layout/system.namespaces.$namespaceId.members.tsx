import { useQuery } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useEffect } from "react"

import { tenantApi } from "@/api/tenantApi"
import { DataTable } from "@/components/Common/DataTable"
import AddNamespaceMember from "@/components/System/AddNamespaceMember"
import { buildNamespaceMemberColumns } from "@/components/System/namespaceMemberColumns"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute(
  "/_layout/system/namespaces/$namespaceId/members",
)({
  component: NamespaceMembersPage,
})

function NamespaceMembersPage() {
  const { namespaceId } = Route.useParams()
  const { user } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (user?.is_superuser) {
      return
    }
    localStorage.setItem("selected_namespace_id", namespaceId)
  }, [namespaceId, user?.is_superuser])

  const { data: usersData, error } = useQuery({
    queryKey: ["namespace-users", namespaceId],
    queryFn: () => tenantApi.readNamespaceUsers(namespaceId),
  })

  useEffect(() => {
    if (!error) {
      return
    }
    navigate({ to: "/" })
  }, [error, navigate])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">空间成员管理</h1>
          <p className="text-muted-foreground">
            管理当前空间的成员、角色和关联状态。
          </p>
        </div>
        <AddNamespaceMember namespaceId={namespaceId} />
      </div>
      <DataTable
        columns={buildNamespaceMemberColumns(namespaceId)}
        data={usersData?.data ?? []}
      />
    </div>
  )
}
