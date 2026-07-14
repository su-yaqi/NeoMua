import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"

import { mcpServersApi, tenantApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

type NodeMcpTarget = {
  id: string
  server_slug: string
  revision: number
  transport: string
  status: string
  secret_ref: string | null
}

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
  const mcpTargets = useQuery({
    queryKey: ["node-mcp-targets", node.data?.runtime_profile_id],
    enabled: Boolean(node.data?.runtime_profile_id),
    queryFn: async (): Promise<NodeMcpTarget[]> => {
      const servers = await mcpServersApi.list()
      const details = await Promise.all(
        servers.data.map(async (server) => {
          const revisions = await mcpServersApi.revisions(server.id)
          return Promise.all(
            revisions.data.map(async (revision) => ({
              server,
              revision: await mcpServersApi.getRevision(
                server.id,
                Number(revision.revision),
              ),
            })),
          )
        }),
      )
      return details.flat(2).flatMap(({ server, revision }) =>
        ((revision.targets as Array<Record<string, unknown>> | undefined) ?? [])
          .filter(
            (target) =>
              target.runtime_profile_id === node.data?.runtime_profile_id,
          )
          .map((target) => ({
            id: String(target.id),
            server_slug: server.slug,
            revision: Number(revision.revision),
            transport: String(revision.transport),
            status: String(target.status),
            secret_ref: target.secret_ref ? String(target.secret_ref) : null,
          })),
      )
    },
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
          <div>
            Claude CLI：
            {node.data?.harness_capabilities.claude_code?.cli_version ??
              "unknown"}
          </div>
          <div>
            Claude SDK：
            {node.data?.harness_capabilities.claude_code?.sdk_version ??
              "unknown"}
          </div>
          <div>
            Harness：
            {node.data?.harness_capabilities.claude_code?.harness_version ??
              "unknown"}
          </div>
          <div>最后心跳：{node.data?.last_seen_at ?? "从未"}</div>
          <div>运行时配置：{node.data?.runtime_profile_id ?? "未配置"}</div>
          <div className="sm:col-span-2">
            MCP executable inventory：
            {node.data?.harness_capabilities.mcp_executables?.join(", ") ||
              "无"}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>MCP 本地凭证与目标就绪状态</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {mcpTargets.data?.length ? (
            mcpTargets.data.map((target) => (
              <div className="rounded border p-3" key={String(target.id)}>
                <p className="font-medium">
                  MCP 标识：{String(target.server_slug)} · Revision{" "}
                  {String(target.revision)}
                </p>
                <p className="text-sm">
                  {String(target.transport)} · {String(target.status)}
                </p>
                <p className="text-xs text-muted-foreground">
                  secret_ref：{target.secret_ref ? "已配置" : "缺失"}
                </p>
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">
              此节点尚无 MCP 目标绑定。
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
