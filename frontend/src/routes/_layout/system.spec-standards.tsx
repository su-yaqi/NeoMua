import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { type SpecStandard, workspaceApi } from "@/api/tenantApi"
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
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/spec-standards")({
  component: SpecStandardsPage,
})

function SpecStandardsPage() {
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
  const [initialVersion, setInitialVersion] = useState("1.0.0")
  const [initialManifest, setInitialManifest] = useState(
    JSON.stringify(
      {
        directory_conventions: [],
        required_files: [],
        templates: {},
        validation_rules: [],
      },
      null,
      2,
    ),
  )
  const standards = useQuery({
    queryKey: ["spec-standards"],
    queryFn: workspaceApi.listSpecStandards,
  })
  const create = useMutation({
    mutationFn: () => {
      let manifest: Record<string, unknown>
      try {
        manifest = JSON.parse(initialManifest) as Record<string, unknown>
      } catch {
        throw new Error("manifest_json_invalid")
      }
      return workspaceApi.createSpecStandardComplete({
        scope_type: "namespace",
        name,
        slug,
        description: description || null,
        version: initialVersion,
        manifest,
      })
    },
    onSuccess: () => {
      setName("")
      setSlug("")
      setDescription("")
      setInitialVersion("1.0.0")
      setIsCreateOpen(false)
      queryClient.invalidateQueries({ queryKey: ["spec-standards"] })
      toast.success("Spec 标准已创建")
    },
    onError: (error) =>
      toast.error(
        error instanceof Error && error.message === "manifest_json_invalid"
          ? "Manifest 必须是有效 JSON"
          : "Spec 标准或首个版本创建失败",
      ),
  })
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Spec 标准</h1>
          <p className="text-muted-foreground">
            标准版本不可变；项目只绑定精确版本，升级先预览差异且不会自动写 Git。
          </p>
        </div>
        {canManage && (
          <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
            <DialogTrigger asChild>
              <Button className="shrink-0">
                <Plus className="mr-2 size-4" />
                新建标准
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>新建 namespace 标准</DialogTitle>
                <DialogDescription>
                  一次创建标准身份和首个不可变版本，失败时不会留下空标准。
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 py-4">
                <Input
                  aria-label="标准名称"
                  placeholder="标准名称"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
                <Input
                  aria-label="标准标识"
                  placeholder="例如 engineering-standard"
                  value={slug}
                  onChange={(event) => setSlug(event.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  标准标识用于链接及系统引用，创建后不可修改。
                </p>
                <Input
                  placeholder="标准说明"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
                <Input
                  aria-label="首个版本"
                  placeholder="首个版本，例如 1.0.0"
                  value={initialVersion}
                  onChange={(event) => setInitialVersion(event.target.value)}
                />
                <textarea
                  aria-label="首个版本 Manifest"
                  className="min-h-56 w-full rounded-md border bg-background p-3 font-mono text-xs"
                  value={initialManifest}
                  onChange={(event) => setInitialManifest(event.target.value)}
                />
              </div>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="outline" disabled={create.isPending}>
                    取消
                  </Button>
                </DialogClose>
                <Button
                  disabled={
                    !name ||
                    !slug ||
                    !initialVersion ||
                    !initialManifest ||
                    create.isPending
                  }
                  onClick={() => create.mutate()}
                >
                  {create.isPending ? "创建中..." : "创建"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {standards.data?.data.map((standard) => (
          <StandardCard key={standard.id} standard={standard} />
        ))}
      </div>
    </div>
  )
}

function StandardCard({ standard }: { standard: SpecStandard }) {
  const queryClient = useQueryClient()
  const [version, setVersion] = useState("")
  const [manifestText, setManifestText] = useState(
    JSON.stringify(
      {
        directory_conventions: [],
        required_files: [],
        templates: {},
        validation_rules: [],
      },
      null,
      2,
    ),
  )
  const versions = useQuery({
    queryKey: ["spec-standard-versions", standard.id],
    queryFn: () => workspaceApi.listSpecStandardVersions(standard.id),
  })
  const publish = useMutation({
    mutationFn: async () => {
      let manifest: Record<string, unknown>
      try {
        manifest = JSON.parse(manifestText) as Record<string, unknown>
      } catch {
        throw new Error("manifest_json_invalid")
      }
      return workspaceApi.publishSpecStandardVersion(standard.id, {
        version,
        manifest,
      })
    },
    onSuccess: () => {
      setVersion("")
      queryClient.invalidateQueries({
        queryKey: ["spec-standard-versions", standard.id],
      })
    },
    onError: (error) =>
      toast.error(
        error instanceof Error && error.message === "manifest_json_invalid"
          ? "Manifest 必须是有效 JSON"
          : "不可变版本发布失败",
      ),
  })
  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between">
          <CardTitle>{standard.name}</CardTitle>
          <Badge>{standard.scope_type}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          标准标识：{standard.slug}
        </p>
        <div className="space-y-2">
          {versions.data?.data.map((item) => (
            <div className="flex items-center justify-between" key={item.id}>
              <span className="font-mono text-sm">{item.version}</span>
              <Badge
                variant={item.status === "active" ? "default" : "secondary"}
              >
                {item.status}
              </Badge>
            </div>
          ))}
          {versions.data?.count === 0 && (
            <p className="text-sm text-muted-foreground">尚未发布版本</p>
          )}
        </div>
        <div className="space-y-2 border-t pt-4">
          <Input
            placeholder="版本，例如 1.0.0"
            value={version}
            onChange={(event) => setVersion(event.target.value)}
          />
          <textarea
            className="min-h-40 w-full rounded-md border bg-background p-3 font-mono text-xs"
            value={manifestText}
            onChange={(event) => setManifestText(event.target.value)}
          />
          <Button
            disabled={!version || publish.isPending}
            onClick={() => publish.mutate()}
          >
            发布不可变版本
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
