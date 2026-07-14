import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  agentsApi,
  mcpServersApi,
  pluginsApi,
  skillsApi,
  toolsApi,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

type McpRevisionOption = {
  id: string
  revision: number
  transport: string
  deprecated: boolean
  server_id: string
  server_slug: string
  targets: Array<Record<string, unknown>>
  availableTools: string[]
}

export default function AgentCapabilities({
  agentId,
  canManage,
}: {
  agentId: string
  canManage: boolean
}) {
  const client = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { data: current } = useQuery({
    queryKey: ["agent-capabilities", agentId],
    queryFn: () => agentsApi.getCapabilities(agentId),
  })
  const { data: skillVersions = [] } = useQuery({
    queryKey: ["skill-version-options"],
    queryFn: async () => {
      const identities = await skillsApi.list()
      return (
        await Promise.all(
          identities.data.map((item) => skillsApi.versions(item.id)),
        )
      ).flatMap((item) => item.data)
    },
  })
  const { data: pluginVersions = [] } = useQuery({
    queryKey: ["plugin-version-options"],
    queryFn: async () => {
      const identities = await pluginsApi.list()
      const details = await Promise.all(
        identities.data.map((item) => pluginsApi.get(item.id)),
      )
      return details.flatMap(
        (item) =>
          (item.versions as Array<Record<string, unknown>> | undefined) ?? [],
      )
    },
  })
  const { data: mcpRevisions = [] } = useQuery({
    queryKey: ["mcp-revision-options"],
    queryFn: async (): Promise<McpRevisionOption[]> => {
      const identities = await mcpServersApi.list()
      const revisions = (
        await Promise.all(
          identities.data.map(async (identity) => {
            const listed = await mcpServersApi.revisions(identity.id)
            return listed.data.map((item) => ({
              id: String(item.id),
              revision: Number(item.revision),
              transport: String(item.transport),
              deprecated: Boolean(item.deprecated),
              server_id: identity.id,
              server_slug: identity.slug,
            }))
          }),
        )
      ).flat()
      return Promise.all(
        revisions.map(async (revision) => {
          const detail = await mcpServersApi.getRevision(
            String(revision.server_id),
            Number(revision.revision),
          )
          const targets = (detail.targets ?? []) as Array<
            Record<string, unknown>
          >
          const histories = await Promise.all(
            targets.map((target) =>
              mcpServersApi.validations(String(target.id)),
            ),
          )
          const availableTools = Array.from(
            new Set(
              histories.flatMap((history) =>
                (history.data as Array<Record<string, unknown>>)
                  .filter((attempt) => attempt.status === "verified")
                  .flatMap((attempt) =>
                    (
                      (attempt.tools as Array<Record<string, unknown>>) ?? []
                    ).map((tool) => String(tool.qualified_name)),
                  ),
              ),
            ),
          ).sort()
          return { ...revision, targets, availableTools }
        }),
      )
    },
  })
  const { data: toolCatalog } = useQuery({
    queryKey: ["tools"],
    queryFn: toolsApi.list,
  })
  const [skills, setSkills] = useState<string[]>([])
  const [plugins, setPlugins] = useState<string[]>([])
  const [mcpBindings, setMcpBindings] = useState<Record<string, string[]>>({})
  const [toolPolicies, setToolPolicies] = useState<Record<string, string>>({})
  useEffect(() => {
    if (!current) return
    setSkills(
      (current.skills as Array<{ skill_version_id: string }>).map(
        (item) => item.skill_version_id,
      ),
    )
    setPlugins(
      (current.plugins as Array<{ plugin_version_id: string }>).map(
        (item) => item.plugin_version_id,
      ),
    )
    setMcpBindings(
      Object.fromEntries(
        (
          current.mcp as Array<{
            revision_id: string
            allowed_tools: string[]
          }>
        ).map((item) => [item.revision_id, item.allowed_tools]),
      ),
    )
    setToolPolicies(
      Object.fromEntries(
        (current.tools as Array<{ tool_key: string; policy: string }>).map(
          (item) => [item.tool_key, item.policy],
        ),
      ),
    )
  }, [current])
  const save = useMutation({
    mutationFn: async (kind: "skills" | "plugins" | "mcp" | "tools") => {
      const revision = Number(current.revision)
      if (kind === "skills")
        return agentsApi.setSkills(agentId, revision, skills)
      if (kind === "plugins")
        return agentsApi.setPlugins(agentId, revision, plugins)
      if (kind === "mcp")
        return agentsApi.setMcp(
          agentId,
          revision,
          Object.entries(mcpBindings).map(([revision_id, allowed_tools]) => ({
            revision_id,
            allowed_tools,
          })),
        )
      return agentsApi.setTools(
        agentId,
        revision,
        Object.entries(toolPolicies).map(([tool_key, policy]) => ({
          tool_key,
          policy,
        })),
      )
    },
    onSuccess: () => {
      showSuccessToast("能力绑定已保存，草稿 revision 已推进")
      client.invalidateQueries({ queryKey: ["agent-capabilities", agentId] })
      client.invalidateQueries({ queryKey: ["agent-draft", agentId] })
    },
    onError: handleError.bind(showErrorToast),
  })
  if (!current) return <p>加载能力目录…</p>
  const toggle = (
    value: string,
    values: string[],
    setValues: (next: string[]) => void,
  ) =>
    setValues(
      values.includes(value)
        ? values.filter((item) => item !== value)
        : [...values, value],
    )
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>精确 Skill Version</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {skillVersions.map((item) => (
            <label className="flex gap-2" key={item.id}>
              <input
                type="checkbox"
                checked={skills.includes(item.id)}
                disabled={!canManage || item.deprecated}
                onChange={() => toggle(item.id, skills, setSkills)}
              />
              {item.version} · {item.content_sha256.slice(0, 12)}
            </label>
          ))}
          {canManage && (
            <Button onClick={() => save.mutate("skills")}>保存 Skills</Button>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>精确 Plugin Version</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {pluginVersions.map((item) => {
            const id = String(item.id)
            return (
              <label className="flex gap-2" key={id}>
                <input
                  type="checkbox"
                  checked={plugins.includes(id)}
                  disabled={!canManage || Boolean(item.deprecated)}
                  onChange={() => toggle(id, plugins, setPlugins)}
                />
                {String(item.version)} ·{" "}
                {String(item.manifest_digest).slice(0, 12)}
              </label>
            )
          })}
          {canManage && (
            <Button onClick={() => save.mutate("plugins")}>保存 Plugins</Button>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>精确 MCP Revision</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {mcpRevisions.map((item) => {
            const id = String(item.id)
            const selected = id in mcpBindings
            const hasVerifiedTarget = item.targets.some(
              (target) => String(target.status) === "verified",
            )
            return (
              <div className="space-y-2 rounded border p-3" key={id}>
                <label className="flex gap-2">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={
                      !canManage ||
                      Boolean(item.deprecated) ||
                      !hasVerifiedTarget
                    }
                    onChange={() =>
                      setMcpBindings((value) => {
                        if (id in value) {
                          const next = { ...value }
                          delete next[id]
                          return next
                        }
                        return { ...value, [id]: [] }
                      })
                    }
                  />
                  MCP 标识：{String(item.server_slug)} · Revision{" "}
                  {String(item.revision)} · {String(item.transport)}
                </label>
                {!hasVerifiedTarget && (
                  <p className="text-xs text-destructive">
                    此 Revision 尚无已验证目标，不能绑定。
                  </p>
                )}
                {selected && (
                  <div className="ml-6 space-y-1">
                    <p className="text-xs text-muted-foreground">
                      显式允许的 MCP Tools
                    </p>
                    {item.availableTools.map((tool) => (
                      <label className="flex gap-2 text-sm" key={tool}>
                        <input
                          type="checkbox"
                          checked={(mcpBindings[id] ?? []).includes(tool)}
                          disabled={!canManage}
                          onChange={() =>
                            setMcpBindings((value) => ({
                              ...value,
                              [id]: (value[id] ?? []).includes(tool)
                                ? value[id].filter((name) => name !== tool)
                                : [...(value[id] ?? []), tool],
                            }))
                          }
                        />
                        {tool}
                      </label>
                    ))}
                    {!item.availableTools.length && (
                      <p className="text-xs text-muted-foreground">
                        已验证目标尚未发现 Tool。
                      </p>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          {canManage && (
            <Button onClick={() => save.mutate("mcp")}>保存 MCP</Button>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Agent Tool 意图</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {toolCatalog?.data.map((item) => {
            const key = String(item.tool_key)
            return (
              <div
                className="flex items-center justify-between gap-2"
                key={key}
              >
                <span>{key}</span>
                <select
                  disabled={!canManage}
                  value={toolPolicies[key] ?? "inherit"}
                  onChange={(event) =>
                    setToolPolicies((value) => ({
                      ...value,
                      [key]: event.target.value,
                    }))
                  }
                >
                  <option value="inherit">inherit</option>
                  <option value="allow">allow</option>
                  <option value="deny">deny</option>
                </select>
              </div>
            )
          })}
          {canManage && (
            <Button onClick={() => save.mutate("tools")}>保存 Tools</Button>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
