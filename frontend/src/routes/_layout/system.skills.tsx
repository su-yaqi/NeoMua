import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Outlet, useRouterState } from "@tanstack/react-router"
import { ChevronRight, FileCode2, Plus } from "lucide-react"
import { useState } from "react"
import { skillsApi } from "@/api/tenantApi"
import { CreateSkillCompleteDialog } from "@/components/Agents/CompleteCapabilityDialogs"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/skills")({
  component: Page,
})

function Page() {
  const { user } = useAuth()
  const [search, setSearch] = useState("")
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const { data } = useQuery({
    queryKey: ["skills", search],
    queryFn: () => skillsApi.list(search ? { q: search } : undefined),
  })
  if (pathname !== "/system/skills") return <Outlet />
  const namespace = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespace,
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Skill 管理</h1>
          <p className="text-muted-foreground">
            管理 Skill 身份、工作区草稿、不可变版本及运行时同步状态。
          </p>
        </div>
        {canManage && <CreateSkillCompleteDialog />}
      </div>
      <Input
        className="max-w-md"
        placeholder="搜索名称、唯一标识或说明"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <Card>
        <CardContent className="p-0">
          <div className="grid grid-cols-[minmax(220px,1fr)_100px_80px_80px_120px_140px_32px] gap-3 border-b px-4 py-3 text-xs font-medium text-muted-foreground">
            <span>Skill</span>
            <span>当前版本</span>
            <span>引用</span>
            <span>文件</span>
            <span>草稿</span>
            <span>Runtime 同步</span>
            <span />
          </div>
          {data?.data.map((item) => (
            <button
              type="button"
              key={item.id}
              className="grid w-full grid-cols-[minmax(220px,1fr)_100px_80px_80px_120px_140px_32px] items-center gap-3 border-b px-4 py-4 text-left transition-colors last:border-b-0 hover:bg-muted/50"
              onClick={() => window.location.assign(`/system/skills/${item.id}`)}
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="rounded-md bg-primary/10 p-2 text-primary">
                  <FileCode2 className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">{item.name}</span>
                  <code className="text-xs text-muted-foreground">{item.slug}</code>
                </span>
              </span>
              <span className="text-sm">v{item.current_version.version}</span>
              <span className="text-sm">{item.reference_count}</span>
              <span className="text-sm">{item.file_count}</span>
              <span className="text-sm">
                {item.draft_dirty ? `未验证 r${item.draft_revision}` : "已验证"}
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {Object.keys(item.sync_summary).length
                  ? Object.entries(item.sync_summary)
                      .map(([status, count]) => `${status} ${count}`)
                      .join(" · ")
                  : "未订阅"}
              </span>
              <ChevronRight className="size-4 text-muted-foreground" />
            </button>
          ))}
          {!data?.count && (
            <div className="flex flex-col items-center gap-3 p-12 text-muted-foreground">
              <Plus className="size-6" />
              <p>还没有 Skill</p>
              {canManage && <Button variant="outline">从右上角新建</Button>}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
