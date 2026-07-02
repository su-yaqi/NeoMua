import type { ColumnDef } from "@tanstack/react-table"

import type { TenantUser } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { NamespaceMemberActionsMenu } from "./NamespaceMemberActionsMenu"

export function buildNamespaceMemberColumns(
  namespaceId: string,
): ColumnDef<TenantUser>[] {
  return [
    {
      accessorKey: "full_name",
      header: "姓名",
      cell: ({ row }) => (
        <span
          className={cn(!row.original.full_name && "text-muted-foreground")}
        >
          {row.original.full_name || "未填写"}
        </span>
      ),
    },
    {
      accessorKey: "email",
      header: "邮箱",
    },
    {
      accessorKey: "namespace_roles",
      header: "空间角色",
      cell: ({ row }) => (
        <Badge variant="outline">
          {row.original.namespace_roles[0]?.role ?? "user"}
        </Badge>
      ),
    },
    {
      accessorKey: "is_superuser",
      header: "全局角色",
      cell: ({ row }) =>
        row.original.is_superuser ? (
          <Badge>Superuser</Badge>
        ) : (
          <span className="text-muted-foreground">平台用户</span>
        ),
    },
    {
      accessorKey: "is_active",
      header: "状态",
      cell: ({ row }) => (
        <span className={row.original.is_active ? "" : "text-muted-foreground"}>
          {row.original.is_active ? "启用" : "禁用"}
        </span>
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <NamespaceMemberActionsMenu
            namespaceId={namespaceId}
            user={row.original}
          />
        </div>
      ),
    },
  ]
}
