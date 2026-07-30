import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useMemo, useState } from "react"

import { type LlmProviderConfig, tenantApi } from "@/api/tenantApi"
import { DataTable } from "@/components/Common/DataTable"
import { buildLlmProviderColumns } from "@/components/LlmProviders/columns"
import ProviderConfigDialog from "@/components/LlmProviders/ProviderConfigDialog"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/llm-providers")({
  component: SystemLlmProvidersPage,
})

function SystemLlmProvidersPage() {
  const { user, isLoading } = useAuth()
  const [editingConfig, setEditingConfig] = useState<LlmProviderConfig | null>(
    null,
  )
  const [editingOpen, setEditingOpen] = useState(false)
  const selectedNamespaceId = localStorage.getItem("selected_namespace_id")

  const catalogQuery = useQuery({
    queryKey: ["llm-provider-catalog", selectedNamespaceId],
    queryFn: tenantApi.readLlmProviderCatalog,
    enabled: Boolean(user) && Boolean(selectedNamespaceId),
  })
  const configsQuery = useQuery({
    queryKey: ["llm-provider-configs", selectedNamespaceId],
    queryFn: tenantApi.readLlmProviderConfigs,
    enabled: Boolean(user) && Boolean(selectedNamespaceId),
  })

  const columns = useMemo(
    () =>
      buildLlmProviderColumns((config) => {
        setEditingConfig(config)
        setEditingOpen(true)
      }),
    [],
  )

  if (isLoading) {
    return null
  }

  if (!selectedNamespaceId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>未选择空间</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          请先在顶部空间选择器中选择一个空间，再进入大模型接入配置页。
        </CardContent>
      </Card>
    )
  }

  if (catalogQuery.isError || configsQuery.isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>无法加载大模型接入配置</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>请确认当前账号具有该空间的管理员权限，且后端接口可用。</p>
          <p>{catalogQuery.error?.message ?? configsQuery.error?.message}</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">大模型接入配置</h1>
          <p className="text-muted-foreground">
            为当前空间维护多供应商接入参数、连接校验和可用模型集合。
          </p>
        </div>
        {catalogQuery.data ? (
          <ProviderConfigDialog
            catalog={catalogQuery.data.data}
            triggerLabel="新增配置"
          />
        ) : null}
      </div>

      <DataTable columns={columns} data={configsQuery.data?.data ?? []} />

      {catalogQuery.data ? (
        <ProviderConfigDialog
          catalog={catalogQuery.data.data}
          config={editingConfig}
          open={editingOpen}
          onOpenChange={(nextOpen) => {
            setEditingOpen(nextOpen)
            if (!nextOpen) {
              setEditingConfig(null)
            }
          }}
        />
      ) : null}
    </div>
  )
}
