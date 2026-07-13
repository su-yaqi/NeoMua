import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { skillsApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/skills/$skillId")({
  component: Page,
})
function Page() {
  const { skillId } = Route.useParams()
  const { user } = useAuth()
  const role = user?.namespace_roles?.find(
    (item) =>
      item.namespace_id === localStorage.getItem("selected_namespace_id"),
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const [version, setVersion] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const { data, refetch } = useQuery({
    queryKey: ["skill", skillId],
    queryFn: () => skillsApi.get(skillId),
  })
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">{String(data?.name ?? "Skill")}</h1>
      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>上传不可变版本</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Input
              placeholder="1.0.0"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            />
            <Input
              type="file"
              accept=".zip"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <Button
              disabled={!version || !file}
              onClick={async () => {
                if (file) {
                  await skillsApi.upload(skillId, version, file)
                  await refetch()
                }
              }}
            >
              上传并扫描
            </Button>
          </CardContent>
        </Card>
      )}
      {(
        (data?.versions as Array<Record<string, unknown>> | undefined) ?? []
      ).map((item) => (
        <Card key={String(item.id)}>
          <CardHeader>
            <CardTitle>
              v{String(item.version)} ·{" "}
              {String(item.content_sha256).slice(0, 12)}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p>状态：{item.deprecated ? "deprecated" : "active"}</p>
            <pre className="overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(item.manifest ?? {}, null, 2)}
            </pre>
            {canManage && !item.deprecated && (
              <Button
                variant="destructive"
                onClick={async () => {
                  if (window.confirm("确认废弃此不可变 Skill Version？")) {
                    await skillsApi.deprecate(skillId, String(item.version))
                    await refetch()
                  }
                }}
              >
                Deprecate
              </Button>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
