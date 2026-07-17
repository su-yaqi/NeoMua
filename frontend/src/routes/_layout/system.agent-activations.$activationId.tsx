import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { releasesApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute(
  "/_layout/system/agent-activations/$activationId",
)({ component: Page })
function Page() {
  const { activationId } = Route.useParams()
  const { user } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()
  const role = user?.namespace_roles?.find(
    (item) =>
      item.namespace_id === localStorage.getItem("selected_namespace_id"),
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const { data } = useQuery({
    queryKey: ["agent-activation", activationId],
    queryFn: () => releasesApi.getActivation(activationId),
    refetchInterval: 3000,
  })
  const action = useMutation({
    mutationFn: ({
      kind,
      deploymentId,
    }: {
      kind: "retry" | "rollback"
      deploymentId: string
    }) =>
      kind === "retry"
        ? releasesApi.retryDeployment(deploymentId)
        : releasesApi.rollbackDeployment(deploymentId),
    onSuccess: (result, variables) => {
      showSuccessToast(
        variables.kind === "retry" ? "部署重试已创建" : "可审计回滚批次已创建",
      )
      queryClient.invalidateQueries({
        queryKey: ["agent-activation", activationId],
      })
      if (variables.kind === "rollback" && result?.id) {
        window.location.assign(`/system/agent-activations/${result.id}`)
      }
    },
    onError: handleError.bind(showErrorToast),
  })
  const deployments = (data?.deployments ?? []) as Array<
    Record<string, unknown>
  >
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">激活批次</h1>
      <Card>
        <CardHeader>
          <CardTitle>状态：{String(data?.status ?? "")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {deployments.map((deployment) => {
            const status = String(deployment.status)
            const retryable = ["failed", "expired", "incompatible"].includes(
              status,
            )
            return (
              <div
                className="space-y-2 rounded border p-3"
                key={String(deployment.id)}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="font-medium">
                      目标 Runtime{" "}
                      {String(
                        deployment.runtime_instance_id ||
                          deployment.runtime_profile_id,
                      )}
                    </p>
                    <p className="text-sm">
                      attempt {String(deployment.attempt)} · {status}
                    </p>
                  </div>
                  {canManage && (
                    <div className="flex gap-2">
                      {retryable && (
                        <Button
                          variant="outline"
                          onClick={() =>
                            window.confirm(
                              "确认重新执行实时预检并创建新 attempt？",
                            ) &&
                            action.mutate({
                              kind: "retry",
                              deploymentId: String(deployment.id),
                            })
                          }
                        >
                          Retry
                        </Button>
                      )}
                      <Button
                        variant="destructive"
                        onClick={() =>
                          window.confirm(
                            "确认创建显式回滚？这只影响后续新任务，不终止在途任务。",
                          ) &&
                          action.mutate({
                            kind: "rollback",
                            deploymentId: String(deployment.id),
                          })
                        }
                      >
                        Rollback
                      </Button>
                    </div>
                  )}
                </div>
                <pre className="overflow-auto rounded bg-muted p-2 text-xs">
                  {JSON.stringify(
                    {
                      capability_fingerprint: deployment.capability_fingerprint,
                      applied_digest: deployment.applied_digest,
                      error: deployment.error,
                      expires_at: deployment.expires_at,
                    },
                    null,
                    2,
                  )}
                </pre>
              </div>
            )
          })}
        </CardContent>
      </Card>
    </div>
  )
}
