import { Link } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"

import type { NamespacePublic } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { NamespaceActionsMenu } from "./NamespaceActionsMenu"

export const columns: ColumnDef<NamespacePublic>[] = [
  {
    accessorKey: "name",
    header: "名称",
  },
  {
    accessorKey: "code",
    header: "编码",
  },
  {
    accessorKey: "is_active",
    header: "状态",
    cell: ({ row }) => (row.original.is_active ? "启用" : "禁用"),
  },
  {
    id: "actions",
    header: () => <span className="sr-only">Actions</span>,
    cell: ({ row }) => (
      <div className="flex items-center justify-end gap-2">
        <Button asChild size="sm" variant="outline">
          <Link
            to="/system/namespaces/$namespaceId/members"
            params={{ namespaceId: row.original.id }}
          >
            成员管理
          </Link>
        </Button>
        <NamespaceActionsMenu namespace={row.original} />
      </div>
    ),
  },
]
