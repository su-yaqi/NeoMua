import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/projects")({
  component: ProjectsPage,
})

function ProjectsPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const namespaceId = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId,
  )?.role
  const canManage = Boolean(
    user?.is_superuser || role === "admin" || role === "developer",
  )
  const [name, setName] = useState("")
  const [slug, setSlug] = useState("")
  const projects = useQuery({
    queryKey: ["projects", namespaceId],
    queryFn: () => workspaceApi.listProjects(true),
    enabled: Boolean(namespaceId),
  })
  const createProject = useMutation({
    mutationFn: () =>
      workspaceApi.createProject({ name, slug, member_ids: [] }),
    onSuccess: () => {
      setName("")
      setSlug("")
      queryClient.invalidateQueries({ queryKey: ["projects"] })
      toast.success("项目已创建")
    },
    onError: () => toast.error("项目创建失败，请检查名称、slug 和权限"),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">项目</h1>
        <p className="text-muted-foreground">
          管理项目成员、多仓库、Spec 位置与项目任务；归档后历史保持只读。
        </p>
      </div>
      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>新建项目</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
            <Input
              aria-label="项目名称"
              placeholder="项目名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <Input
              aria-label="项目 slug"
              placeholder="project-slug"
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
            />
            <Button
              disabled={!name || !slug || createProject.isPending}
              onClick={() => createProject.mutate()}
            >
              创建
            </Button>
          </CardContent>
        </Card>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {projects.data?.data.map((project) => (
          <Link
            key={project.id}
            to="/projects/$projectId"
            params={{ projectId: project.id }}
          >
            <Card className="h-full transition-colors hover:border-primary">
              <CardHeader>
                <div className="flex items-center justify-between gap-3">
                  <CardTitle>{project.name}</CardTitle>
                  <Badge
                    variant={
                      project.status === "active" ? "default" : "secondary"
                    }
                  >
                    {project.status === "active" ? "活跃" : "已归档"}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-2 text-sm text-muted-foreground">
                <p>{project.description || "暂无描述"}</p>
                <p>
                  {project.member_ids.length} 位成员 · {project.slug}
                </p>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
      {!projects.isLoading && projects.data?.count === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            暂无可见项目。
          </CardContent>
        </Card>
      )}
    </div>
  )
}
