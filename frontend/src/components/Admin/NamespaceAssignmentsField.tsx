import type { NamespacePublic, NamespaceRole, UserNamespaceAssignment } from "@/client/tenantApi"
import { Checkbox } from "@/components/ui/checkbox"
import { FormControl, FormItem, FormLabel } from "@/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const namespaceRoleOptions: Array<{ label: string; value: NamespaceRole }> = [
  { label: "管理员", value: "admin" },
  { label: "开发者", value: "developer" },
  { label: "普通用户", value: "user" },
]

interface NamespaceAssignmentsFieldProps {
  namespaces: NamespacePublic[]
  value: UserNamespaceAssignment[]
  onChange: (value: UserNamespaceAssignment[]) => void
}

export function NamespaceAssignmentsField({
  namespaces,
  value,
  onChange,
}: NamespaceAssignmentsFieldProps) {
  const assignments = value ?? []

  const toggleNamespace = (namespaceId: string, checked: boolean) => {
    if (checked) {
      onChange([...assignments, { namespace_id: namespaceId, role: "user" }])
      return
    }
    onChange(assignments.filter((item) => item.namespace_id !== namespaceId))
  }

  const updateRole = (namespaceId: string, role: NamespaceRole) => {
    onChange(
      assignments.map((item) =>
        item.namespace_id === namespaceId ? { ...item, role } : item,
      ),
    )
  }

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div>
        <p className="font-medium">空间关联</p>
        <p className="text-sm text-muted-foreground">
          勾选用户可访问的空间，并设置其在该空间内的角色。
        </p>
      </div>

      {namespaces.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          当前还没有可分配的空间，请先创建空间。
        </p>
      ) : (
        <div className="space-y-3">
          {namespaces.map((namespace) => {
            const currentAssignment = assignments.find(
              (item) => item.namespace_id === namespace.id,
            )
            return (
              <div
                key={namespace.id}
                className="flex flex-col gap-3 rounded-md border p-3 md:flex-row md:items-center md:justify-between"
              >
                <FormItem className="flex items-center gap-3 space-y-0">
                  <FormControl>
                    <Checkbox
                      checked={Boolean(currentAssignment)}
                      onCheckedChange={(checked) =>
                        toggleNamespace(namespace.id, checked === true)
                      }
                    />
                  </FormControl>
                  <div>
                    <FormLabel className="font-normal">{namespace.name}</FormLabel>
                    <p className="text-xs text-muted-foreground">
                      编码：{namespace.code}
                    </p>
                  </div>
                </FormItem>

                <Select
                  value={currentAssignment?.role ?? "user"}
                  disabled={!currentAssignment}
                  onValueChange={(nextRole: NamespaceRole) =>
                    updateRole(namespace.id, nextRole)
                  }
                >
                  <SelectTrigger className="w-full md:w-40">
                    <SelectValue placeholder="选择角色" />
                  </SelectTrigger>
                  <SelectContent>
                    {namespaceRoleOptions.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
