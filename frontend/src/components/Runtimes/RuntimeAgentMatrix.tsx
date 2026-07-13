import { useQuery } from "@tanstack/react-query"
import { releasesApi } from "@/api/tenantApi"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export default function RuntimeAgentMatrix() {
  const bindings = useQuery({
    queryKey: ["runtime-agents"],
    queryFn: releasesApi.runtimeAgents,
    refetchInterval: 5000,
  })
  const rows = (bindings.data?.data ?? []) as Array<Record<string, unknown>>
  return (
    <Card>
      <CardHeader>
        <CardTitle>已激活 Agent 矩阵</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="p-2">运行时</th>
                  <th>Agent</th>
                  <th>当前 Release</th>
                  <th>Resolved Spec</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr className="border-b" key={String(row.id)}>
                    <td className="p-2">
                      {String(row.runtime_name)} · {String(row.runtime_type)}
                    </td>
                    <td>
                      {String(row.agent_name)} ({String(row.agent_slug)})
                    </td>
                    <td>v{String(row.current_release_version)}</td>
                    <td>
                      <code className="text-xs">
                        {String(row.applied_digest).slice(0, 16)}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            当前空间尚未在任何运行时激活 Agent Release。
          </p>
        )}
      </CardContent>
    </Card>
  )
}
