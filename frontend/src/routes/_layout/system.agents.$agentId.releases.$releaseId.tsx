import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { releasesApi, runtimeInstancesApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute(
  "/_layout/system/agents/$agentId/releases/$releaseId",
)({ component: Page })
function Page() {
  const { releaseId } = Route.useParams()
  const { user } = useAuth()
  const { showErrorToast } = useCustomToast()
  const role = user?.namespace_roles?.find(
    (item) =>
      item.namespace_id === localStorage.getItem("selected_namespace_id"),
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const [target, setTarget] = useState("")
  const [precheck, setPrecheck] = useState<{
    id: string
    runtime_instance_id: string
    deployable: boolean
    precheck_digest: string
    checks: Record<string, unknown>
    expires_at: string
  } | null>(null)
  const { data: release } = useQuery({
    queryKey: ["agent-release", releaseId],
    queryFn: () => releasesApi.get(releaseId),
  })
  const { data: runtimes } = useQuery({
    queryKey: ["runtime-instances"],
    queryFn: runtimeInstancesApi.list,
  })
  const options =
    runtimes?.data.filter(
      (runtime) => runtime.enabled && runtime.status === "available",
    ) || []
  const precheckMutation = useMutation({
    mutationFn: () => releasesApi.precheckActivation(releaseId, target),
    onSuccess: (result) => setPrecheck(result),
    onError: handleError.bind(showErrorToast),
  })
  const activateMutation = useMutation({
    mutationFn: () => {
      if (!precheck) throw new Error("activation_precheck_required")
      return releasesApi.activate(releaseId, {
        runtime_instance_id: target,
        precheck_id: precheck.id,
        precheck_digest: precheck.precheck_digest,
      })
    },
    onSuccess: (activation) =>
      window.location.assign(`/system/agent-activations/${activation.id}`),
    onError: handleError.bind(showErrorToast),
  })
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">
        Agent Release v{String(release?.version ?? "")}
      </h1>
      <Card>
        <CardHeader>
          <CardTitle>ResolvedAgentSpec</CardTitle>
        </CardHeader>
        <CardContent>
          <code className="text-xs">
            {String(release?.resolved_spec_digest ?? "")}
          </code>
          <div className="mt-3 rounded border p-3">
            <p className="text-sm font-medium">允许使用的 Skill 身份</p>
            <p className="mb-2 text-xs text-muted-foreground">
              Release 不锁定内容版本；每个任务使用 Runtime
              当时已同步并应用的版本。
            </p>
            {(
              ((release?.dependency_lock as Record<string, unknown> | undefined)
                ?.skills as Array<Record<string, unknown>> | undefined) ?? []
            ).map((skill) => (
              <div
                className="flex justify-between py-1 text-sm"
                key={String(skill.id)}
              >
                <span>{String(skill.slug)}</span>
                <span className="text-muted-foreground">
                  {((skill.sources as string[] | undefined) ?? []).join("、")}
                </span>
              </div>
            ))}
          </div>
          <pre className="mt-2 max-h-96 overflow-auto rounded bg-muted p-3 text-xs">
            {JSON.stringify(release?.manifest ?? {}, null, 2)}
          </pre>
          <details className="mt-3">
            <summary>依赖锁、签名与来源链</summary>
            <pre className="mt-2 max-h-96 overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(
                {
                  dependency_lock: release?.dependency_lock,
                  components: release?.components,
                  manifest_digest: release?.manifest_digest,
                  signature: release?.signature,
                  signing_public_key: release?.signing_public_key,
                  signature_valid: release?.signature_valid,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </CardContent>
      </Card>
      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>显式激活目标</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Select
              value={target}
              onValueChange={(value) => {
                setTarget(value)
                setPrecheck(null)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择一个 Runtime Instance" />
              </SelectTrigger>
              <SelectContent>
                {options.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.name} · {option.engine_type} ·{" "}
                    {option.location_type}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={!target || precheckMutation.isPending}
                onClick={() => precheckMutation.mutate()}
              >
                运行目标预检
              </Button>
              <Button
                disabled={
                  !target ||
                  precheck?.deployable !== true ||
                  activateMutation.isPending
                }
                onClick={() => activateMutation.mutate()}
              >
                创建激活批次
              </Button>
            </div>
            {precheck && (
              <pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-xs">
                {JSON.stringify(precheck, null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
