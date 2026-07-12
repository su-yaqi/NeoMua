import { createFileRoute } from "@tanstack/react-router"
import HarnessProfileSheet from "@/components/Agents/HarnessProfileSheet"
import { Button } from "@/components/ui/button"
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
import useAuth from "@/hooks/useAuth"
import { useQuery } from "@tanstack/react-query"
import { harnessProfilesApi, type HarnessProfilePublic } from "@/api/tenantApi"
import { useState } from "react"

export const Route = createFileRoute("/_layout/system/harnesses")({
  component: HarnessesPage,
})

function HarnessesPage() {
  const { user } = useAuth()
  const [editing, setEditing] = useState<HarnessProfilePublic | null>(null)
  const [creating, setCreating] = useState(false)
  const namespaceId = localStorage.getItem("selected_namespace_id")
  const { data, isLoading } = useQuery({
    queryKey: ["harness-profiles"],
    queryFn: harnessProfilesApi.list,
    enabled: !!namespaceId,
  })
  if (!namespaceId)
    return (
      <Card>
        <CardHeader>
          <CardTitle>未选择空间</CardTitle>
        </CardHeader>
        <CardContent>请先选择空间。</CardContent>
      </Card>
    )
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId
  )?.role
  const visible = Boolean(
    user?.is_superuser || role === "admin" || role === "developer"
  )
  if (user && !visible) return null
  const canManage = Boolean(user?.is_superuser || role === "admin")

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Harness 配置</h1>
          <p className="text-muted-foreground">
            管理可复用的 Claude Harness Profile 与结构化 CLI 配置。
          </p>
        </div>
        {canManage && (
          <Button onClick={() => setCreating(true)}>创建 Profile</Button>
        )}
      </div>
      <Card>
        <CardContent>
          {isLoading ? (
            <p className="text-muted-foreground">加载中…</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>Harness</TableHead>
                  <TableHead>CLI 约束</TableHead>
                  <TableHead>SDK 约束</TableHead>
                  <TableHead>引用</TableHead>
                  <TableHead>状态</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.data.map((p) => (
                  <TableRow
                    key={p.id}
                    onClick={() => setEditing(p)}
                    className="cursor-pointer"
                  >
                    <TableCell className="font-medium">{p.name}</TableCell>
                    <TableCell>{p.harness_type}</TableCell>
                    <TableCell className="font-mono text-sm">
                      {p.cli_version_constraint}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {p.sdk_version_constraint}
                    </TableCell>
                    <TableCell>
                      {p.referenced_by_agents ? "被引用" : "—"}
                    </TableCell>
                    <TableCell>
                      {p.archived ? (
                        <Badge variant="secondary">已归档</Badge>
                      ) : (
                        <Badge>启用</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {(creating || editing) && (
        <HarnessProfileSheet
          profile={editing}
          open={creating || editing !== null}
          onOpenChange={(o) => {
            if (!o) {
              setCreating(false)
              setEditing(null)
            }
          }}
          canManage={canManage}
        />
      )}
    </div>
  )
}
