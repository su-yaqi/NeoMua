import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import {
  type RuntimeInstanceSummary,
  runtimeInstancesApi,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

const managementLabels: Record<
  RuntimeInstanceSummary["management_type"],
  string
> = {
  platform_builtin: "平台内置",
  service_managed: "服务节点",
  client_discovered: "客户端发现",
  legacy_manual: "历史 Runtime",
}

export default function RuntimeInstancePanel({
  canManage,
}: {
  canManage: boolean
}) {
  const runtimes = useQuery({
    queryKey: ["runtime-instances"],
    queryFn: runtimeInstancesApi.list,
    refetchInterval: 5000,
  })
  const [selectedId, setSelectedId] = useState("")
  const queryClient = useQueryClient()
  const control = useMutation({
    mutationFn: (runtime: RuntimeInstanceSummary) =>
      runtime.enabled
        ? runtimeInstancesApi.pause(runtime.id)
        : runtimeInstancesApi.resume(runtime.id),
    onSuccess: (runtime) => {
      queryClient.invalidateQueries({ queryKey: ["runtime-instances"] })
      queryClient.invalidateQueries({
        queryKey: ["runtime-instance", runtime.id],
      })
    },
  })
  const groups: Array<{
    key: RuntimeInstanceSummary["management_type"]
    title: string
    description: string
  }> = [
    {
      key: "platform_builtin",
      title: "平台内置",
      description: "由平台发布和大模型配置自动协调。",
    },
    {
      key: "service_managed",
      title: "服务节点",
      description: "由签名发行清单安装并由 NeoMua 管理生命周期。",
    },
    {
      key: "client_discovered",
      title: "客户端",
      description:
        "节点上已有的 Claude Code / Codex，仅发现和控制，不负责安装。",
    },
    {
      key: "legacy_manual",
      title: "历史 Runtime",
      description: "缺少 v0.10 来源证据，仅保留历史读取。",
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime 目录</CardTitle>
        <p className="mt-1 text-sm text-muted-foreground">
          平台 Runtime 自动提供；服务节点和客户端 Runtime
          由正式安装及发现协议注册，无需填写命令或安装标识。
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {groups.map((group) => {
          const rows =
            runtimes.data?.data.filter(
              (runtime) => runtime.management_type === group.key,
            ) || []
          if (rows.length === 0) return null
          return (
            <section key={group.key} className="space-y-3">
              <div>
                <h3 className="font-semibold">{group.title}</h3>
                <p className="text-xs text-muted-foreground">
                  {group.description}
                </p>
              </div>
              {rows.map((runtime) => (
                <div key={runtime.id} className="rounded-lg border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="font-medium">{runtime.name}</p>
                      <p className="font-mono text-xs text-muted-foreground">
                        {runtime.id}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline">
                        {managementLabels[runtime.management_type]}
                      </Badge>
                      <Badge variant="outline">{runtime.engine_type}</Badge>
                      <Badge>{runtime.evidence_state || runtime.status}</Badge>
                    </div>
                  </div>
                  <div className="mt-3 grid gap-2 text-xs text-muted-foreground md:grid-cols-4">
                    <span>Engine {runtime.engine_version || "待发现"}</span>
                    <span>Adapter {runtime.adapter_version}</span>
                    <span>可用模型 {runtime.available_model_count}</span>
                    <span>{runtime.enabled ? "已启用" : "未启用"}</span>
                  </div>
                  {runtime.management_type === "platform_builtin" && (
                    <p className="mt-3 text-xs text-muted-foreground">
                      无需配置。请在“大模型接入配置”中启用并验证模型，系统会自动形成平台路由。
                    </p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        setSelectedId((current) =>
                          current === runtime.id ? "" : runtime.id,
                        )
                      }
                    >
                      {selectedId === runtime.id
                        ? "收起状态"
                        : "查看状态与模型"}
                    </Button>
                    {canManage &&
                    ["client_discovered", "service_managed"].includes(
                      runtime.management_type,
                    ) ? (
                      <Button
                        size="sm"
                        variant={runtime.enabled ? "destructive" : "default"}
                        disabled={control.isPending}
                        onClick={() => {
                          const action = runtime.enabled ? "暂停" : "恢复"
                          if (
                            window.confirm(
                              `${action} ${runtime.name} 的新任务调度？在途任务和本机引擎不会被修改。`,
                            )
                          )
                            control.mutate(runtime)
                        }}
                      >
                        {runtime.enabled ? "暂停调度" : "恢复调度"}
                      </Button>
                    ) : null}
                  </div>
                  {selectedId === runtime.id && (
                    <RuntimeInstanceDetail runtime={runtime} />
                  )}
                </div>
              ))}
            </section>
          )
        })}
        {control.isError ? (
          <p className="text-sm text-destructive">{control.error.message}</p>
        ) : null}
        {runtimes.data?.data.length === 0 && (
          <p className="text-sm text-muted-foreground">
            正在初始化 Runtime 目录。
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function RuntimeInstanceDetail({
  runtime,
}: {
  runtime: RuntimeInstanceSummary
}) {
  const detail = useQuery({
    queryKey: ["runtime-instance", runtime.id],
    queryFn: () => runtimeInstancesApi.get(runtime.id),
    refetchInterval: 5000,
  })
  const bindings = useQuery({
    queryKey: ["runtime-model-bindings", runtime.id],
    queryFn: () => runtimeInstancesApi.listBindings(runtime.id),
    refetchInterval: 5000,
  })
  const latestConfiguration = detail.data?.configurations[0]
  const latestReport = detail.data?.capability_reports[0]

  return (
    <div className="mt-4 space-y-4 border-t pt-4">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded border p-3">
          <p className="font-medium">当前能力证据</p>
          <pre className="mt-2 max-h-48 overflow-auto text-xs">
            {JSON.stringify(latestReport || { status: "尚未上报" }, null, 2)}
          </pre>
        </div>
        <div className="rounded border p-3">
          <p className="font-medium">系统配置证据（只读）</p>
          <div className="mt-2 space-y-1 text-xs text-muted-foreground">
            <p>来源：{latestConfiguration?.origin || "等待系统生成"}</p>
            <p>修订：{latestConfiguration?.revision || "-"}</p>
            <p>状态：{latestConfiguration?.status || "-"}</p>
            <p>执行引用：{latestConfiguration?.adapter_execution_ref || "-"}</p>
          </div>
        </div>
      </div>

      <div className="space-y-3 rounded border p-3">
        <p className="font-medium">模型路由（只读）</p>
        {bindings.data?.data.map((binding) => (
          <div
            key={binding.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded bg-muted/40 p-2 text-sm"
          >
            <div>
              <p>{binding.engine_model_id}</p>
              <p className="text-xs text-muted-foreground">
                {binding.route_type}:{binding.route_key}
              </p>
            </div>
            <Badge>{binding.status}</Badge>
          </div>
        ))}
        {!bindings.data?.data.length && (
          <p className="text-sm text-muted-foreground">
            尚无经过验证的模型路由。
          </p>
        )}
      </div>
    </div>
  )
}
