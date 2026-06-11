import type { ColumnDef } from "@tanstack/react-table"

import type { LlmProviderConfig } from "@/client/tenantApi"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

const statusLabel: Record<LlmProviderConfig["validation_status"], string> = {
  unverified: "未校验",
  success: "校验成功",
  failed: "校验失败",
  unsupported: "不支持校验",
}

const statusVariant: Record<
  LlmProviderConfig["validation_status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  unverified: "outline",
  success: "default",
  failed: "destructive",
  unsupported: "secondary",
}

export function buildLlmProviderColumns(
  onEdit: (config: LlmProviderConfig) => void,
): ColumnDef<LlmProviderConfig>[] {
  return [
    {
      accessorKey: "config_name",
      header: "配置名称",
    },
    {
      accessorKey: "provider_display_name",
      header: "供应商",
    },
    {
      accessorKey: "base_url",
      header: "接入地址",
      cell: ({ row }) => (
        <span className="block max-w-[320px] truncate" title={row.original.base_url}>
          {row.original.base_url}
        </span>
      ),
    },
    {
      accessorKey: "secret_masked",
      header: "凭证摘要",
      cell: ({ row }) => row.original.secret_masked ?? "未配置",
    },
    {
      accessorKey: "validation_status",
      header: "校验状态",
      cell: ({ row }) => (
        <Badge variant={statusVariant[row.original.validation_status]}>
          {statusLabel[row.original.validation_status]}
        </Badge>
      ),
    },
    {
      id: "model_count",
      header: "可用模型",
      cell: ({ row }) =>
        row.original.models.filter((item) => item.is_enabled).length,
    },
    {
      accessorKey: "enabled",
      header: "启用",
      cell: ({ row }) => (row.original.enabled ? "是" : "否"),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="outline"
            onClick={() => onEdit(row.original)}
          >
            编辑
          </Button>
        </div>
      ),
    },
  ]
}
