import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"

import { runtimeInstancesApi, tenantApi } from "@/api/tenantApi"
import RuntimeSkillMatrix from "@/components/Runtimes/RuntimeSkillMatrix"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/runtimes_/nodes/$nodeId")(
  {
    component: RuntimeNodePage,
  },
)

function RuntimeNodePage() {
  const { nodeId } = Route.useParams()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const namespaceId = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId,
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const node = useQuery({
    queryKey: ["runtime-node", nodeId],
    queryFn: () => tenantApi.readRuntimeNode(nodeId),
    refetchInterval: 20_000,
  })
  const runtimes = useQuery({
    queryKey: ["node-runtime-instances", nodeId],
    queryFn: () => runtimeInstancesApi.listNode(nodeId),
    refetchInterval: 5000,
  })
  const observations = useQuery({
    queryKey: ["runtime-discovery-observations", nodeId],
    queryFn: () => runtimeInstancesApi.listDiscoveryObservations(nodeId),
    enabled: node.data?.management_mode === "client",
    refetchInterval: 5000,
  })
  const refresh = useMutation({
    mutationFn: () => runtimeInstancesApi.requestDiscoveryRefresh(nodeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runtime-node", nodeId] })
      queryClient.invalidateQueries({
        queryKey: ["runtime-discovery-observations", nodeId],
      })
    },
  })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Runtime Node 详情</h1>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2">
            {node.data?.name}
            <Badge variant={node.data?.online ? "default" : "secondary"}>
              {node.data?.online ? "在线" : "离线"}
            </Badge>
          </CardTitle>
          {canManage && node.data?.management_mode === "client" ? (
            <Button
              size="sm"
              variant="outline"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              请求重新扫描
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div>主机：{node.data?.hostname}</div>
          <div>
            系统：{node.data?.os_name}/{node.data?.architecture}
          </div>
          <div>节点守护进程版本：{node.data?.agent_version}</div>
          <div>最后心跳：{node.data?.last_seen_at ?? "从未"}</div>
          <div>管理模式：{node.data?.management_mode}</div>
          <div>Bootstrap：{node.data?.bootstrap_status ?? "历史节点"}</div>
          <div>
            发现代次：{node.data?.discovery_generation ?? 0} / 请求{" "}
            {node.data?.discovery_requested_generation ?? 0}
          </div>
          <div>
            安装 receipt：{node.data?.current_installation_receipt_id ?? "无"}
          </div>
          {node.data?.adapter_registry_digest ? (
            <div className="break-all text-xs sm:col-span-2">
              Adapter Registry：{node.data.adapter_registry_digest}
            </div>
          ) : null}
          {node.data?.installation_manifest_digest ? (
            <div className="break-all text-xs sm:col-span-2">
              发行清单：{node.data.installation_manifest_digest}
            </div>
          ) : null}
          <p className="text-sm text-muted-foreground sm:col-span-2">
            Node 只代表机器与安全边界；Claude Code、Codex
            等安装由发现协议分别注册为 Runtime Instance。
          </p>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <h2 className="text-lg font-semibold">已发现 Runtime Instance</h2>
        {runtimes.data?.data.map((runtime) => (
          <Card key={runtime.id}>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                {runtime.name}
                <Badge variant="outline">{runtime.engine_type}</Badge>
                <Badge>{runtime.evidence_state || runtime.status}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-2 text-sm sm:grid-cols-3">
                <span>Engine {runtime.engine_version || "待发现"}</span>
                <span>Adapter {runtime.adapter_version}</span>
                <span>可用模型 {runtime.available_model_count}</span>
              </div>
              <RuntimeSkillMatrix runtimeId={runtime.id} />
            </CardContent>
          </Card>
        ))}
        {!runtimes.data?.data.length && (
          <p className="text-sm text-muted-foreground">
            节点尚未上报受信任的 Runtime 安装。
          </p>
        )}
      </div>

      {node.data?.management_mode === "client" ? (
        <Card>
          <CardHeader>
            <CardTitle>发现观察与诊断</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {observations.data?.data.slice(0, 50).map((observation) => (
              <div
                key={observation.id}
                className="grid gap-2 rounded border p-3 text-xs md:grid-cols-[100px_1fr_140px]"
              >
                <Badge
                  variant={
                    observation.status === "available"
                      ? "default"
                      : "destructive"
                  }
                >
                  {observation.status}
                </Badge>
                <span className="break-all">
                  {observation.installation_key}
                </span>
                <span>generation {observation.generation}</span>
                {observation.status !== "available" ? (
                  <pre className="overflow-auto md:col-span-3">
                    {JSON.stringify(observation.evidence, null, 2)}
                  </pre>
                ) : null}
              </div>
            ))}
            {!observations.data?.data.length ? (
              <p className="text-sm text-muted-foreground">
                尚无受设备签名的发现观察。
              </p>
            ) : null}
            {refresh.isError ? (
              <p className="text-sm text-destructive">
                {refresh.error.message}
              </p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
