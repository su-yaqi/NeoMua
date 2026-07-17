import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useMemo, useState } from "react"
import { mcpServersApi, runtimeInstancesApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

type McpServerDetail = Record<string, unknown> & {
  name?: string
  revisions: Array<Record<string, unknown>>
}

export const Route = createFileRoute(
  "/_layout/system/mcp-servers/$mcpServerId",
)({ component: Page })

function Page() {
  const { mcpServerId } = Route.useParams()
  const { user } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()
  const role = user?.namespace_roles?.find(
    (item) =>
      item.namespace_id === localStorage.getItem("selected_namespace_id"),
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const [transport, setTransport] = useState("streamable_http")
  const [endpoint, setEndpoint] = useState("")
  const [executableKey, setExecutableKey] = useState("")
  const [targetRevisionId, setTargetRevisionId] = useState("")
  const [runtimeInstanceId, setRuntimeInstanceId] = useState("")
  const [secretRef, setSecretRef] = useState("")
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({})

  const server = useQuery({
    queryKey: ["mcp-server", mcpServerId],
    queryFn: async (): Promise<McpServerDetail> => {
      const identity = (await mcpServersApi.get(mcpServerId)) as Record<
        string,
        unknown
      >
      const revisions = await Promise.all(
        (
          (identity.revisions as Array<Record<string, unknown>> | undefined) ??
          []
        ).map(async (revision) => {
          const detail = await mcpServersApi.getRevision(
            mcpServerId,
            Number(revision.revision),
          )
          const targets = await Promise.all(
            (
              (detail.targets as Array<Record<string, unknown>> | undefined) ??
              []
            ).map(async (target) => ({
              ...target,
              validations: await mcpServersApi.validations(String(target.id)),
              runtime: await mcpServersApi.runtime(String(target.id)),
            })),
          )
          return { ...detail, targets }
        }),
      )
      return { ...identity, revisions }
    },
    refetchInterval: 5000,
  })
  const runtimes = useQuery({
    queryKey: ["runtime-instances"],
    queryFn: runtimeInstancesApi.list,
  })
  const runtimeOptions = useMemo(
    () =>
      runtimes.data?.data
        .filter((runtime) => runtime.enabled && runtime.status === "available")
        .map((runtime) => ({
          id: runtime.id,
          label: `${runtime.name}（${runtime.engine_type}）`,
          kind: runtime.location_type,
        })) || [],
    [runtimes.data],
  )
  const selectedRuntime = runtimeOptions.find(
    (item) => item.id === runtimeInstanceId,
  )
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["mcp-server", mcpServerId] })
  const revisionMutation = useMutation({
    mutationFn: () =>
      mcpServersApi.createRevision(mcpServerId, {
        transport,
        config:
          transport === "stdio"
            ? { executable_key: executableKey, args: [] }
            : { endpoint },
      }),
    onSuccess: () => {
      showSuccessToast("不可变 MCP Revision 已创建")
      setEndpoint("")
      setExecutableKey("")
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const targetMutation = useMutation({
    mutationFn: () =>
      mcpServersApi.createTarget(targetRevisionId, {
        runtime_instance_id: runtimeInstanceId,
        ...(selectedRuntime?.kind === "node" ? { secret_ref: secretRef } : {}),
      }),
    onSuccess: () => {
      showSuccessToast("MCP 目标绑定已创建")
      setSecretRef("")
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const action = useMutation({
    mutationFn: async ({
      kind,
      targetId,
    }: {
      kind: "secret" | "validate" | "restart"
      targetId: string
    }) => {
      if (kind === "secret") {
        const raw = secretInputs[targetId] ?? ""
        const parsed = JSON.parse(raw) as Record<string, string>
        return mcpServersApi.setTargetSecret(targetId, parsed)
      }
      if (kind === "restart") return mcpServersApi.restartRuntime(targetId)
      return mcpServersApi.validateTarget(targetId)
    },
    onSuccess: () => {
      showSuccessToast("MCP 目标操作已提交")
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })

  const revisions =
    (server.data?.revisions as Array<Record<string, unknown>> | undefined) ?? []
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">
        {String(server.data?.name ?? "MCP Server")}
      </h1>
      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>创建不可变 Revision</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 md:grid-cols-3">
            <select
              className="rounded border bg-background px-3"
              value={transport}
              onChange={(event) => setTransport(event.target.value)}
            >
              <option value="streamable_http">streamable_http</option>
              <option value="sse">sse</option>
              <option value="stdio">stdio</option>
            </select>
            <Input
              placeholder={
                transport === "stdio"
                  ? "inventory executable key"
                  : "https://mcp.example.com"
              }
              value={transport === "stdio" ? executableKey : endpoint}
              onChange={(event) =>
                transport === "stdio"
                  ? setExecutableKey(event.target.value)
                  : setEndpoint(event.target.value)
              }
            />
            <Button
              disabled={
                revisionMutation.isPending ||
                (transport === "stdio" ? !executableKey : !endpoint)
              }
              onClick={() => revisionMutation.mutate()}
            >
              创建 Revision
            </Button>
          </CardContent>
        </Card>
      )}
      {canManage && revisions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>绑定明确运行时目标</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 md:grid-cols-4">
            <select
              className="rounded border bg-background px-3"
              value={targetRevisionId}
              onChange={(event) => setTargetRevisionId(event.target.value)}
            >
              <option value="">选择 Revision</option>
              {revisions.map((revision) => (
                <option key={String(revision.id)} value={String(revision.id)}>
                  revision {String(revision.revision)} ·{" "}
                  {String(revision.transport)}
                </option>
              ))}
            </select>
            <select
              className="rounded border bg-background px-3"
              value={runtimeInstanceId}
              onChange={(event) => setRuntimeInstanceId(event.target.value)}
            >
              <option value="">选择平台或节点</option>
              {runtimeOptions.map((runtime) => (
                <option key={runtime.id} value={runtime.id}>
                  {runtime.label}
                </option>
              ))}
            </select>
            <Input
              disabled={selectedRuntime?.kind !== "node"}
              placeholder="节点本地 secret_ref"
              value={secretRef}
              onChange={(event) => setSecretRef(event.target.value)}
            />
            <Button
              disabled={
                targetMutation.isPending ||
                !targetRevisionId ||
                !runtimeInstanceId ||
                (selectedRuntime?.kind === "node" && !secretRef)
              }
              onClick={() => targetMutation.mutate()}
            >
              创建目标绑定
            </Button>
          </CardContent>
        </Card>
      )}
      {revisions.map((revision) => (
        <Card key={String(revision.id)}>
          <CardHeader>
            <CardTitle>
              Revision {String(revision.revision)} ·{" "}
              {String(revision.transport)}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <pre className="overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(revision.config ?? {}, null, 2)}
            </pre>
            {(
              (revision.targets as
                | Array<Record<string, unknown>>
                | undefined) ?? []
            ).map((target) => {
              const targetId = String(target.id)
              const runtime = target.runtime as Record<string, unknown>
              const validations = target.validations as {
                data?: Array<Record<string, unknown>>
              }
              const isPlatform =
                runtimeOptions.find(
                  (item) => item.id === target.runtime_instance_id,
                )?.kind === "platform"
              return (
                <div className="space-y-2 rounded border p-3" key={targetId}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <p className="font-medium">
                        {runtimeOptions.find(
                          (item) => item.id === target.runtime_instance_id,
                        )?.label ?? String(target.runtime_instance_id)}
                      </p>
                      <p className="text-sm">状态：{String(target.status)}</p>
                    </div>
                    {canManage && (
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          onClick={() =>
                            action.mutate({ kind: "validate", targetId })
                          }
                        >
                          校验并发现 Tools
                        </Button>
                        {Boolean(runtime?.instance) && (
                          <Button
                            variant="outline"
                            onClick={() =>
                              window.confirm("确认显式重启此 MCP Runtime？") &&
                              action.mutate({ kind: "restart", targetId })
                            }
                          >
                            重启 Runtime
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                  {canManage && isPlatform && (
                    <div className="flex gap-2">
                      <Input
                        type="password"
                        placeholder='凭证 JSON，例如 {"Authorization":"Bearer ..."}'
                        value={secretInputs[targetId] ?? ""}
                        onChange={(event) =>
                          setSecretInputs((value) => ({
                            ...value,
                            [targetId]: event.target.value,
                          }))
                        }
                      />
                      <Button
                        disabled={!secretInputs[targetId]}
                        onClick={() =>
                          action.mutate({ kind: "secret", targetId })
                        }
                      >
                        写入/轮换凭证
                      </Button>
                    </div>
                  )}
                  <details>
                    <summary>Runtime 状态与脱敏事件</summary>
                    <pre className="mt-2 overflow-auto rounded bg-muted p-2 text-xs">
                      {JSON.stringify(runtime ?? {}, null, 2)}
                    </pre>
                  </details>
                  <details>
                    <summary>校验历史与 Tool 快照</summary>
                    <pre className="mt-2 overflow-auto rounded bg-muted p-2 text-xs">
                      {JSON.stringify(validations?.data ?? [], null, 2)}
                    </pre>
                  </details>
                </div>
              )
            })}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
