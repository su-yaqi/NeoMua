import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import { tenantApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import EnrollNodeDialog from "./EnrollNodeDialog"

export default function NodeTable({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const nodes = useQuery({
    queryKey: ["runtime-nodes"],
    queryFn: tenantApi.readRuntimeNodes,
    refetchInterval: 20_000,
  })
  const bootstrapSessions = useQuery({
    queryKey: ["runtime-node-bootstrap-sessions"],
    queryFn: tenantApi.readNodeBootstrapSessions,
    enabled: canManage,
    refetchInterval: 5000,
  })
  const revoke = useMutation({
    mutationFn: tenantApi.revokeNodeCredential,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["runtime-nodes"] }),
  })
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>节点运行时</CardTitle>
        {canManage ? (
          <div className="flex gap-2">
            <EnrollNodeDialog mode="service" />
            <EnrollNodeDialog mode="client" />
          </div>
        ) : null}
      </CardHeader>
      <CardContent>
        {nodes.data?.data.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="p-2">节点</th>
                  <th>状态</th>
                  <th>系统</th>
                  <th>版本</th>
                  <th>类型</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {nodes.data.data.map((node) => (
                  <tr className="border-b" key={node.id}>
                    <td className="p-2">
                      <Link
                        className="font-medium underline"
                        to="/system/runtimes/nodes/$nodeId"
                        params={{ nodeId: node.id }}
                      >
                        {node.name}
                      </Link>
                      <div className="text-xs text-muted-foreground">
                        {node.hostname}
                      </div>
                    </td>
                    <td>
                      <Badge variant={node.online ? "default" : "secondary"}>
                        {node.online ? "在线" : "离线"}
                      </Badge>
                    </td>
                    <td>
                      {node.os_name}/{node.architecture}
                    </td>
                    <td>
                      <div>Node {node.agent_version}</div>
                      <div className="text-xs text-muted-foreground">
                        Runtime 实例由节点发现协议独立上报
                      </div>
                    </td>
                    <td>
                      <div className="flex flex-col items-start gap-1">
                        <Badge variant="outline">{node.management_mode}</Badge>
                        <span className="text-xs text-muted-foreground">
                          {node.bootstrap_status ?? "legacy"}
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className="flex justify-end gap-2">
                        {canManage ? (
                          <Button
                            size="sm"
                            variant="destructive"
                            disabled={
                              revoke.isPending || Boolean(node.revoked_at)
                            }
                            onClick={() => {
                              if (
                                window.confirm(`确认吊销 ${node.name} 的凭证？`)
                              )
                                revoke.mutate(node.id)
                            }}
                          >
                            吊销
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            暂无已匹配的节点运行时。
          </p>
        )}
        {canManage && bootstrapSessions.data?.length ? (
          <div className="mt-6 space-y-2 border-t pt-4">
            <h3 className="font-medium">Bootstrap 会话</h3>
            <p className="text-xs text-muted-foreground">
              一次性密文不会再次显示；这里仅保留正式安装阶段、绑定节点和到期时间。
            </p>
            {bootstrapSessions.data.slice(0, 20).map((session) => (
              <div
                key={session.id}
                className="grid gap-2 rounded border p-3 text-xs sm:grid-cols-4"
              >
                <span>{session.management_mode}</span>
                <Badge variant="outline">{session.status}</Badge>
                <span>
                  节点：
                  {session.node_id ? session.node_id.slice(0, 8) : "尚未绑定"}
                </span>
                <span>
                  到期：{new Date(session.expires_at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
