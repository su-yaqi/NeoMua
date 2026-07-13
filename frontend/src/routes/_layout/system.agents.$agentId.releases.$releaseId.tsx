import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { releasesApi, tenantApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  const [targets, setTargets] = useState<string[]>([])
  const [precheck, setPrecheck] = useState<Record<string, unknown> | null>(null)
  const { data: release } = useQuery({
    queryKey: ["agent-release", releaseId],
    queryFn: () => releasesApi.get(releaseId),
  })
  const { data: platform } = useQuery({
    queryKey: ["platform-runtime"],
    queryFn: tenantApi.readPlatformRuntime,
    retry: false,
  })
  const { data: nodes } = useQuery({
    queryKey: ["runtime-nodes"],
    queryFn: tenantApi.readRuntimeNodes,
  })
  const options = [
    ...(platform
      ? [
          {
            id: platform.id,
            label: "平台运行时",
            capability: platform.harness_capabilities,
          },
        ]
      : []),
    ...(nodes?.data
      .filter((node) => node.runtime_profile_id)
      .map((node) => ({
        id: node.runtime_profile_id!,
        label: node.name,
        capability: node.harness_capabilities,
      })) ?? []),
  ]
  const toggle = (id: string) =>
    setTargets((value) => {
      setPrecheck(null)
      return value.includes(id)
        ? value.filter((item) => item !== id)
        : [...value, id]
    })
  const precheckMutation = useMutation({
    mutationFn: () => releasesApi.precheckActivation(releaseId, targets),
    onSuccess: (result) => setPrecheck(result),
    onError: handleError.bind(showErrorToast),
  })
  const activateMutation = useMutation({
    mutationFn: () => releasesApi.activate(releaseId, targets),
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
            {options.map((option) => (
              <label key={option.id} className="flex gap-2">
                <input
                  type="checkbox"
                  checked={targets.includes(option.id)}
                  onChange={() => toggle(option.id)}
                />
                {option.label}
                <code className="text-xs">
                  {JSON.stringify(option.capability)}
                </code>
              </label>
            ))}
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={!targets.length || precheckMutation.isPending}
                onClick={() => precheckMutation.mutate()}
              >
                运行目标预检
              </Button>
              <Button
                disabled={
                  !targets.length ||
                  precheck?.compatible !== true ||
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
