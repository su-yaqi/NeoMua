import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import { tenantApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import DispatchTaskSheet from "./DispatchTaskSheet"
import EnrollNodeDialog from "./EnrollNodeDialog"
import NodeRuntimeConfigDialog from "./NodeRuntimeConfigDialog"

export default function NodeTable({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const nodes = useQuery({
    queryKey: ["runtime-nodes"],
    queryFn: tenantApi.readRuntimeNodes,
    refetchInterval: 20_000,
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
        {canManage ? <EnrollNodeDialog /> : null}
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
                    <td>{node.agent_version}</td>
                    <td>
                      <div className="flex justify-end gap-2">
                        <DispatchTaskSheet node={node} />
                        {canManage ? (
                          <>
                            <NodeRuntimeConfigDialog node={node} />
                            <Button
                              size="sm"
                              variant="destructive"
                              disabled={
                                revoke.isPending || Boolean(node.revoked_at)
                              }
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `确认吊销 ${node.name} 的凭证？`,
                                  )
                                )
                                  revoke.mutate(node.id)
                              }}
                            >
                              吊销
                            </Button>
                          </>
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
      </CardContent>
    </Card>
  )
}
