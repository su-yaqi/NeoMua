import { EllipsisVertical } from "lucide-react"
import { useState } from "react"

import type { NamespacePublic } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import DeleteSystemNamespace from "./DeleteSystemNamespace"
import EditSystemNamespace from "./EditSystemNamespace"

interface NamespaceActionsMenuProps {
  namespace: NamespacePublic
}

export function NamespaceActionsMenu({ namespace }: NamespaceActionsMenuProps) {
  const [open, setOpen] = useState(false)

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon">
          <EllipsisVertical />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          onSelect={(event) => {
            event.preventDefault()
            setOpen(false)
            window.location.assign(`/system/namespaces/${namespace.id}/members`)
          }}
        >
          成员管理
        </DropdownMenuItem>
        <EditSystemNamespace namespace={namespace} />
        <DeleteSystemNamespace
          namespaceId={namespace.id}
          namespaceName={namespace.name}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
