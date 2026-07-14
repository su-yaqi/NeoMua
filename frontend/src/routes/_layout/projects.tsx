import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { tenantApi, workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [name, setName] = useState("")
  const [slug, setSlug] = useState("")
  const [description, setDescription] = useState("")
  const [defaultRuntimeId, setDefaultRuntimeId] = useState("")
  const [memberIds, setMemberIds] = useState<string[]>([])
  const [remoteUrl, setRemoteUrl] = useState("")
  const [repositoryPurpose, setRepositoryPurpose] = useState("")
  const projects = useQuery({
    queryKey: ["projects", namespaceId],
    queryFn: () => workspaceApi.listProjects(true),
    enabled: Boolean(namespaceId),
  })
  const members = useQuery({
    queryKey: ["namespace-users", namespaceId],
    queryFn: () => tenantApi.readNamespaceUsers(namespaceId!),
    enabled: Boolean(namespaceId && canManage),
  })
  const platformRuntime = useQuery({
    queryKey: ["platform-runtime"],
    queryFn: tenantApi.readPlatformRuntime,
    enabled: Boolean(namespaceId && canManage),
    retry: false,
  })
  const runtimeNodes = useQuery({
    queryKey: ["runtime-nodes"],
    queryFn: tenantApi.readRuntimeNodes,
    enabled: Boolean(namespaceId && canManage),
  })
  const runtimeOptions = [
    ...(platformRuntime.data
      ? [{ id: platformRuntime.data.id, name: "平台运行时" }]
      : []),
    ...(runtimeNodes.data?.data
      .filter((node) => node.runtime_profile_id)
      .map((node) => ({
        id: node.runtime_profile_id!,
        name: `${node.name}（节点）`,
      })) ?? []),
  ]
  const createProject = useMutation({
    mutationFn: () =>
      workspaceApi.createProject({
        name,
        slug,
        description: description || null,
        default_runtime_id: defaultRuntimeId,
        member_ids: memberIds,
        initial_repositories:
          remoteUrl && repositoryPurpose
            ? [{ remote_url: remoteUrl, purpose: repositoryPurpose }]
            : [],
      }),
    onSuccess: () => {
      setName("")
      setSlug("")
      setDescription("")
      setDefaultRuntimeId("")
      setMemberIds([])
      setRemoteUrl("")
      setRepositoryPurpose("")
      setIsCreateOpen(false)
      queryClient.invalidateQueries({ queryKey: ["projects"] })
      toast.success("项目已创建")
    },
    onError: () => toast.error("项目创建失败，请检查名称、项目标识和权限"),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">项目</h1>
          <p className="text-muted-foreground">
            管理项目成员、多仓库、Spec 位置与项目任务；归档后历史保持只读。
          </p>
        </div>
        {canManage && (
          <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
            <DialogTrigger asChild>
              <Button className="shrink-0">
                <Plus className="mr-2 size-4" />
                新建项目
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>新建项目</DialogTitle>
                <DialogDescription>
                  一次完成项目身份、默认运行时、初始成员和可选仓库配置。
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-5 py-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label>项目名称</Label>
                    <Input
                      aria-label="项目名称"
                      placeholder="项目名称"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>项目标识</Label>
                    <Input
                      aria-label="项目标识"
                      placeholder="例如 project-delivery"
                      value={slug}
                      onChange={(event) => setSlug(event.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      用于链接及系统引用，创建后不可修改。
                    </p>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>项目说明</Label>
                  <Input
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="项目目标与使用范围"
                  />
                </div>
                <div className="space-y-2">
                  <Label>默认运行时</Label>
                  <select
                    className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                    value={defaultRuntimeId}
                    onChange={(event) =>
                      setDefaultRuntimeId(event.target.value)
                    }
                  >
                    <option value="">选择默认运行时</option>
                    {runtimeOptions.map((runtime) => (
                      <option key={runtime.id} value={runtime.id}>
                        {runtime.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-2">
                  <Label>初始成员</Label>
                  <div className="grid max-h-36 gap-2 overflow-y-auto rounded-md border p-3 md:grid-cols-2">
                    {members.data?.data.map((member) => (
                      <label
                        className="flex items-center gap-2 text-sm"
                        key={member.id}
                      >
                        <input
                          type="checkbox"
                          checked={memberIds.includes(member.id)}
                          onChange={() =>
                            setMemberIds((current) =>
                              current.includes(member.id)
                                ? current.filter((id) => id !== member.id)
                                : [...current, member.id],
                            )
                          }
                        />
                        {member.full_name || member.email}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="space-y-3 rounded-md border p-4">
                  <div>
                    <Label>初始仓库（可选）</Label>
                    <p className="text-xs text-muted-foreground">
                      如暂时没有仓库可留空；填写任一项时必须同时填写完整。
                    </p>
                  </div>
                  <Input
                    value={remoteUrl}
                    onChange={(event) => setRemoteUrl(event.target.value)}
                    placeholder="Git remote URL"
                  />
                  <Input
                    value={repositoryPurpose}
                    onChange={(event) =>
                      setRepositoryPurpose(event.target.value)
                    }
                    placeholder="仓库用途"
                  />
                </div>
              </div>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="outline" disabled={createProject.isPending}>
                    取消
                  </Button>
                </DialogClose>
                <Button
                  disabled={
                    !name ||
                    !slug ||
                    !defaultRuntimeId ||
                    Boolean(remoteUrl) !== Boolean(repositoryPurpose) ||
                    createProject.isPending
                  }
                  onClick={() => createProject.mutate()}
                >
                  {createProject.isPending ? "创建中..." : "创建"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>
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
                  {project.member_ids.length} 位成员 · 项目标识：{project.slug}
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
