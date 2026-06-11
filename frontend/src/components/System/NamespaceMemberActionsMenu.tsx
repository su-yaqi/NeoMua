import { EllipsisVertical } from "lucide-react"
import { useState } from "react"

import type { TenantUser } from "@/client/tenantApi"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import DeleteNamespaceMember from "./DeleteNamespaceMember"
import EditNamespaceMember from "./EditNamespaceMember"

interface NamespaceMemberActionsMenuProps {
  namespaceId: string
  user: TenantUser
}

export function NamespaceMemberActionsMenu({
  namespaceId,
  user,
}: NamespaceMemberActionsMenuProps) {
  const [open, setOpen] = useState(false)

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon">
          <EllipsisVertical />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <EditNamespaceMember
          namespaceId={namespaceId}
          user={user}
          onSuccess={() => setOpen(false)}
        />
        <DeleteNamespaceMember
          namespaceId={namespaceId}
          user={user}
          onSuccess={() => setOpen(false)}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
