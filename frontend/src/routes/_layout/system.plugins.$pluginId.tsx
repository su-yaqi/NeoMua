import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { pluginsApi, skillsApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/plugins/$pluginId")({
  component: Page,
})
function Page() {
  const { pluginId } = Route.useParams()
  const { user } = useAuth()
  const role = user?.namespace_roles?.find(
    (item) =>
      item.namespace_id === localStorage.getItem("selected_namespace_id"),
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const [components, setComponents] = useState("[]")
  const [version, setVersion] = useState("")
  const { data, refetch } = useQuery({
    queryKey: ["plugin", pluginId],
    queryFn: () => pluginsApi.get(pluginId),
  })
  const skillCatalog = useQuery({
    queryKey: ["skills"],
    queryFn: () => skillsApi.list(),
  })
  useEffect(() => {
    if (data?.components)
      setComponents(JSON.stringify(data.components, null, 2))
  }, [data])
  const revision = Number(data?.revision ?? 1)
  const selectedSkillIds = (() => {
    try {
      return new Set(
        (JSON.parse(components) as Array<Record<string, unknown>>)
          .filter((item) => item.type === "skill")
          .map((item) => String(item.skill_id)),
      )
    } catch {
      return new Set<string>()
    }
  })()
  const toggleSkill = (skillId: string) => {
    let parsed: Array<Record<string, unknown>>
    try {
      parsed = JSON.parse(components) as Array<Record<string, unknown>>
    } catch {
      return
    }
    const without = parsed.filter(
      (item) => !(item.type === "skill" && item.skill_id === skillId),
    )
    setComponents(
      JSON.stringify(
        selectedSkillIds.has(skillId)
          ? without
          : [...without, { type: "skill", skill_id: skillId }],
        null,
        2,
      ),
    )
  }
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">
        {String(
          (data?.plugin as Record<string, unknown> | undefined)?.name ??
            "Plugin",
        )}
      </h1>
      <Card>
        <CardHeader>
          <CardTitle>声明式 Contributions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="space-y-2 rounded border p-3">
            <p className="text-sm font-medium">
              Skill 身份贡献（内容版本由 Runtime 独立同步）
            </p>
            {skillCatalog.data?.data.map((skill) => (
              <label className="flex items-center gap-2 text-sm" key={skill.id}>
                <input
                  type="checkbox"
                  checked={selectedSkillIds.has(skill.id)}
                  disabled={!canManage || skill.archived}
                  onChange={() => toggleSkill(skill.id)}
                />
                {skill.name} · {skill.slug} · 当前 v{skill.current_version.version}
              </label>
            ))}
          </div>
          <textarea
            disabled={!canManage}
            className="min-h-64 w-full rounded border bg-background p-3 font-mono text-xs"
            value={components}
            onChange={(event) => setComponents(event.target.value)}
          />
          {canManage && (
            <div className="flex gap-2">
              <Button
                onClick={async () => {
                  await pluginsApi.saveDraft(pluginId, {
                    expected_revision: revision,
                    harness_type: "claude_code",
                    adapter_schema_version: "1.1",
                    adapter_config: {},
                    components: JSON.parse(components),
                  })
                  await refetch()
                }}
              >
                保存草稿
              </Button>
              <Button
                variant="outline"
                onClick={async () => {
                  await pluginsApi.validate(pluginId)
                  await refetch()
                }}
              >
                验证依赖
              </Button>
            </div>
          )}
          {Boolean(data?.validation_result) && (
            <pre className="overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(data?.validation_result, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>
      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>发布不可变版本</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Input
              placeholder="1.0.0"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            />
            <Button
              disabled={!version || data?.validated_revision !== revision}
              onClick={async () => {
                await pluginsApi.publish(pluginId, {
                  draft_revision: revision,
                  version,
                })
                await refetch()
              }}
            >
              发布
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
              Plugin v{String(item.version)} ·{" "}
              {String(item.manifest_digest).slice(0, 12)}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <pre className="overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(
                {
                  manifest: item.manifest,
                  dependency_lock: item.dependency_lock,
                  signature: item.signature,
                  signing_public_key: item.signing_public_key,
                },
                null,
                2,
              )}
            </pre>
            {canManage && !item.deprecated && (
              <Button
                variant="destructive"
                onClick={async () => {
                  if (window.confirm("确认废弃此不可变 Plugin Version？")) {
                    await pluginsApi.deprecate(pluginId, String(item.version))
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
