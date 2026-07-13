import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"
import { type SpecStandard, workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"

export const Route = createFileRoute("/_layout/system/spec-standards")({
  component: SpecStandardsPage,
})

function SpecStandardsPage() {
  const queryClient = useQueryClient()
  const [name, setName] = useState("")
  const [slug, setSlug] = useState("")
  const standards = useQuery({
    queryKey: ["spec-standards"],
    queryFn: workspaceApi.listSpecStandards,
  })
  const create = useMutation({
    mutationFn: () =>
      workspaceApi.createSpecStandard({ scope_type: "namespace", name, slug }),
    onSuccess: () => {
      setName("")
      setSlug("")
      queryClient.invalidateQueries({ queryKey: ["spec-standards"] })
    },
    onError: () => toast.error("Spec 标准创建失败"),
  })
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Spec 标准</h1>
        <p className="text-muted-foreground">
          标准版本不可变；项目只绑定精确版本，升级先预览差异且不会自动写 Git。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>新建 namespace 标准</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
          <Input
            placeholder="标准名称"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            placeholder="standard-slug"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
          />
          <Button disabled={!name || !slug} onClick={() => create.mutate()}>
            创建
          </Button>
        </CardContent>
      </Card>
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
        <p className="text-sm text-muted-foreground">{standard.slug}</p>
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
