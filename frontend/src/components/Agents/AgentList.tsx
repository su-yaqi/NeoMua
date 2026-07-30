import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useState } from "react"
import {
  type AgentListItem,
  agentsApi,
  modelDefinitionsApi,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import CreateAgentDialog from "./CreateAgentDialog"

const statusColor: Record<string, string> = {
  validated: "bg-green-100 text-green-800",
  unvalidated: "bg-gray-100 text-gray-800",
  stale: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-800",
}

export default function AgentList({ canManage }: { canManage: boolean }) {
  const [statusFilter, setStatusFilter] = useState("all")
  const { data, isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: agentsApi.list,
  })
  const models = useQuery({
    queryKey: ["model-definitions"],
    queryFn: modelDefinitionsApi.list,
  })
  const modelNames = new Map(
    models.data?.data.map((item) => [
      item.id,
      item.display_name || item.model_key,
    ]),
  )

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-4">
        <CardTitle>Agent 列表</CardTitle>
        <div className="flex items-center gap-2">
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="active">启用</SelectItem>
              <SelectItem value="archived">已归档</SelectItem>
            </SelectContent>
          </Select>
          {canManage && <CreateAgentDialog />}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-muted-foreground">加载中…</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>Agent 标识</TableHead>
                <TableHead>模型偏好</TableHead>
                <TableHead>草稿修订</TableHead>
                <TableHead>校验状态</TableHead>
                <TableHead>状态</TableHead>
                {canManage && <TableHead>操作</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.data
                .filter(
                  (agent) =>
                    statusFilter === "all" || agent.status === statusFilter,
                )
                .map((agent: AgentListItem) => (
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
                    <TableCell className="font-mono text-sm">
                      {agent.slug}
                    </TableCell>
                    <TableCell>
                      {modelNames.get(
                        agent.preferred_model_definition_id || "",
                      ) ?? "—"}
                    </TableCell>
                    <TableCell>r{agent.draft_revision}</TableCell>
                    <TableCell>
                      <Badge
                        className={statusColor[agent.validation_status] ?? ""}
                      >
                        {agent.validation_status}
                      </Badge>
                    </TableCell>
                    <TableCell>{agent.status}</TableCell>
                    {canManage && (
                      <TableCell>
                        <CreateAgentDialog source={agent} />
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              {data?.data.filter(
                (agent) =>
                  statusFilter === "all" || agent.status === statusFilter,
              ).length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={canManage ? 8 : 7}
                    className="text-center text-muted-foreground"
                  >
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
