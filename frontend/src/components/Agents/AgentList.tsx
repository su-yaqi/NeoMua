import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { agentsApi, type AgentListItem } from "@/api/tenantApi"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import CreateAgentDialog from "./CreateAgentDialog"

const statusColor: Record<string, string> = {
  validated: "bg-green-100 text-green-800",
  unvalidated: "bg-gray-100 text-gray-800",
  stale: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-800",
}

export default function AgentList({ canManage }: { canManage: boolean }) {
  const { data, isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: agentsApi.list,
  })

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Agent 列表</CardTitle>
        {canManage && <CreateAgentDialog />}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-muted-foreground">加载中…</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>Slug</TableHead>
                <TableHead>Harness</TableHead>
                <TableHead>模型</TableHead>
                <TableHead>草稿修订</TableHead>
                <TableHead>校验状态</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.data.map((agent: AgentListItem) => (
                <TableRow key={agent.id}>
                  <TableCell className="font-medium">
                    <Link
                      to="/system/agents/$agentId"
                      params={{ agentId: agent.id }}
                      className="text-blue-600 hover:underline"
                    >
                      {agent.name}
                    </Link>
                  </TableCell>
                  <TableCell className="font-mono text-sm">{agent.slug}</TableCell>
                  <TableCell>{agent.harness_type ?? "—"}</TableCell>
                  <TableCell>{agent.model_id ?? "—"}</TableCell>
                  <TableCell>r{agent.draft_revision}</TableCell>
                  <TableCell>
                    <Badge className={statusColor[agent.validation_status] ?? ""}>
                      {agent.validation_status}
                    </Badge>
                  </TableCell>
                  <TableCell>{agent.status}</TableCell>
                </TableRow>
              ))}
              {data?.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground">
                    暂无 Agent
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
