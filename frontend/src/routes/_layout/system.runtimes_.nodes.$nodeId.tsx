import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"

import { runtimeInstancesApi, tenantApi } from "@/api/tenantApi"
import RuntimeSkillMatrix from "@/components/Runtimes/RuntimeSkillMatrix"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const Route = createFileRoute("/_layout/system/runtimes_/nodes/$nodeId")(
  {
    component: RuntimeNodePage,
  },
)

function RuntimeNodePage() {
  const { nodeId } = Route.useParams()
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

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Runtime Node 详情</h1>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {node.data?.name}
            <Badge variant={node.data?.online ? "default" : "secondary"}>
              {node.data?.online ? "在线" : "离线"}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div>主机：{node.data?.hostname}</div>
          <div>
            系统：{node.data?.os_name}/{node.data?.architecture}
          </div>
          <div>节点守护进程版本：{node.data?.agent_version}</div>
          <div>最后心跳：{node.data?.last_seen_at ?? "从未"}</div>
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
                <Badge>{runtime.status}</Badge>
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
    </div>
  )
}
