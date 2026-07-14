import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export const Route = createFileRoute("/_layout/projects_/$projectId")({
  component: ProjectDetailPage,
})

function ProjectDetailPage() {
  const { projectId } = Route.useParams()
  const queryClient = useQueryClient()
  const [remoteUrl, setRemoteUrl] = useState("")
  const [purpose, setPurpose] = useState("")
  const [specRepositoryId, setSpecRepositoryId] = useState("")
  const [specPath, setSpecPath] = useState("")
  const [specDescription, setSpecDescription] = useState("")
  const [standardVersionId, setStandardVersionId] = useState("")
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => workspaceApi.getProject(projectId),
  })
  const repositories = useQuery({
    queryKey: ["project-repositories", projectId],
    queryFn: () => workspaceApi.listRepositories(projectId),
  })
  const specs = useQuery({
    queryKey: ["project-specs", projectId],
    queryFn: () => workspaceApi.listSpecLocations(projectId),
  })
  const standards = useQuery({
    queryKey: ["spec-standards"],
    queryFn: workspaceApi.listSpecStandards,
  })
  const standardVersions = useQuery({
    queryKey: [
      "spec-standard-versions",
      standards.data?.data.map((row) => row.id),
    ],
    enabled: Boolean(standards.data),
    queryFn: async () => {
      const rows = await Promise.all(
        (standards.data?.data ?? []).map(async (standard) => ({
          standard,
          versions: (await workspaceApi.listSpecStandardVersions(standard.id))
            .data,
        })),
      )
      return rows.flatMap(({ standard, versions }) =>
        versions.map((version) => ({ standard, version })),
      )
    },
  })
  const tasks = useQuery({
    queryKey: ["project-workflows", projectId],
    queryFn: () => workspaceApi.listWorkflowInstances(projectId),
  })
  const addRepository = useMutation({
    mutationFn: () =>
      workspaceApi.createRepository(projectId, {
        remote_url: remoteUrl,
        purpose,
      }),
    onSuccess: () => {
      setRemoteUrl("")
      setPurpose("")
      queryClient.invalidateQueries({
        queryKey: ["project-repositories", projectId],
      })
    },
    onError: () => toast.error("仓库绑定失败"),
  })
  const archive = useMutation({
    mutationFn: () => workspaceApi.archiveProject(projectId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["project", projectId] }),
  })
  const addSpecLocation = useMutation({
    mutationFn: async () => {
      const location = await workspaceApi.createSpecLocation(projectId, {
        repository_id: specRepositoryId,
        path: specPath,
        location_type: "directory",
        description: specDescription,
      })
      if (standardVersionId) {
        await workspaceApi.bindSpecStandard(
          projectId,
          location.id,
          standardVersionId,
        )
      }
      return location
    },
    onSuccess: () => {
      setSpecPath("")
      setSpecDescription("")
      setStandardVersionId("")
      queryClient.invalidateQueries({ queryKey: ["project-specs", projectId] })
    },
    onError: () => toast.error("Spec 位置或精确标准版本绑定失败"),
  })
  if (!project.data)
    return <p className="text-muted-foreground">正在加载项目…</p>
  const readOnly = project.data.status === "archived"

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{project.data.name}</h1>
            <Badge variant={readOnly ? "secondary" : "default"}>
              {project.data.status}
            </Badge>
          </div>
          <p className="text-muted-foreground">
            {project.data.description || `项目标识：${project.data.slug}`}
          </p>
        </div>
        {!readOnly && (
          <Button
            variant="outline"
            onClick={() => {
              if (window.confirm("归档后项目配置和新任务将只读，确认归档？")) {
                archive.mutate()
              }
            }}
          >
            归档项目
          </Button>
        )}
      </div>
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="members">成员</TabsTrigger>
          <TabsTrigger value="repositories">仓库</TabsTrigger>
          <TabsTrigger value="specs">Spec</TabsTrigger>
          <TabsTrigger value="tasks">项目任务</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <Card>
            <CardContent className="py-6">
              {project.data.member_ids.length} 位项目成员；默认 Runtime：
              {project.data.default_runtime_id || "未配置"}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="members">
          <Card>
            <CardHeader>
              <CardTitle>项目成员</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {project.data.member_ids.map((memberId) => (
                <p className="font-mono text-sm" key={memberId}>
                  {memberId}
                </p>
              ))}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="repositories" className="space-y-4">
          {!readOnly && (
            <Card>
              <CardHeader>
                <CardTitle>绑定仓库</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
                <Input
                  placeholder="Git remote URL"
                  value={remoteUrl}
                  onChange={(event) => setRemoteUrl(event.target.value)}
                />
                <textarea
                  className="min-h-10 rounded-md border bg-background px-3 py-2 text-sm"
                  placeholder="仓库用途（必填）"
                  value={purpose}
                  onChange={(event) => setPurpose(event.target.value)}
                />
                <Button
                  disabled={!remoteUrl || !purpose}
                  onClick={() => addRepository.mutate()}
                >
                  添加
                </Button>
              </CardContent>
            </Card>
          )}
          {repositories.data?.data.map((repository) => (
            <Card key={repository.id}>
              <CardContent className="flex items-start justify-between gap-4 py-5">
                <div>
                  <p className="font-medium break-all">
                    {repository.remote_url}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {repository.purpose}
                  </p>
                </div>
                <Badge
                  variant={
                    repository.status === "available" ? "default" : "secondary"
                  }
                >
                  {repository.status}
                </Badge>
              </CardContent>
            </Card>
          ))}
        </TabsContent>
        <TabsContent value="specs" className="space-y-3">
          {!readOnly && repositories.data?.data.length !== 0 && (
            <Card>
              <CardHeader>
                <CardTitle>添加 Spec 位置</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3 md:grid-cols-2">
                <select
                  className="h-10 rounded-md border bg-background px-3 text-sm"
                  value={specRepositoryId}
                  onChange={(event) => setSpecRepositoryId(event.target.value)}
                >
                  <option value="">选择仓库</option>
                  {repositories.data?.data.map((repository) => (
                    <option key={repository.id} value={repository.id}>
                      {repository.remote_url}
                    </option>
                  ))}
                </select>
                <Input
                  placeholder="仓库内路径，例如 context"
                  value={specPath}
                  onChange={(event) => setSpecPath(event.target.value)}
                />
                <Input
                  placeholder="该位置的内容说明"
                  value={specDescription}
                  onChange={(event) => setSpecDescription(event.target.value)}
                />
                <select
                  className="h-10 rounded-md border bg-background px-3 text-sm"
                  value={standardVersionId}
                  onChange={(event) => setStandardVersionId(event.target.value)}
                >
                  <option value="">暂不绑定标准</option>
                  {standardVersions.data?.map(({ standard, version }) => (
                    <option key={version.id} value={version.id}>
                      {standard.name} · {version.version}
                    </option>
                  ))}
                </select>
                <Button
                  disabled={!specRepositoryId || !specPath || !specDescription}
                  onClick={() => addSpecLocation.mutate()}
                >
                  添加并绑定精确版本
                </Button>
              </CardContent>
            </Card>
          )}
          {specs.data?.data.map((spec) => (
            <Card key={spec.id}>
              <CardContent className="py-5">
                <p className="font-medium">{spec.path}</p>
                <p className="text-sm text-muted-foreground">
                  {spec.description} · {spec.location_type} · {spec.status}
                </p>
                <p className="text-sm text-muted-foreground">
                  标准版本：{spec.binding?.standard_version_id || "未绑定"}
                </p>
              </CardContent>
            </Card>
          ))}
          {specs.data?.count === 0 && (
            <Card>
              <CardContent className="py-8 text-muted-foreground">
                尚未配置 Spec 位置。
              </CardContent>
            </Card>
          )}
        </TabsContent>
        <TabsContent value="tasks" className="space-y-3">
          {tasks.data?.data.map((task) => (
            <Link
              key={task.id}
              to="/apps/$workflowSlug/tasks/$instanceId"
              params={{ workflowSlug: task.workflow_slug, instanceId: task.id }}
            >
              <Card className="hover:border-primary">
                <CardContent className="flex justify-between py-5">
                  <span>{task.title}</span>
                  <Badge>{task.status}</Badge>
                </CardContent>
              </Card>
            </Link>
          ))}
        </TabsContent>
      </Tabs>
    </div>
  )
}
