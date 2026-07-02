import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"

import { tenantApi } from "@/api/tenantApi"
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
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">节点运行时详情</h1>
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
          <div>节点版本：{node.data?.agent_version}</div>
          <div>SDK：{node.data?.sdk_version ?? "-"}</div>
          <div>最后心跳：{node.data?.last_seen_at ?? "从未"}</div>
          <div>运行时配置：{node.data?.runtime_profile_id ?? "未配置"}</div>
        </CardContent>
      </Card>
    </div>
  )
}
