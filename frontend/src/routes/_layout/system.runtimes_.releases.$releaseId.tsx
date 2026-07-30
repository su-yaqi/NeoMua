import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"

import { tenantApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute(
  "/_layout/system/runtimes_/releases/$releaseId",
)({ component: ArtifactReleasePage })

function ArtifactReleasePage() {
  const { releaseId } = Route.useParams()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const namespaceId = localStorage.getItem("selected_namespace_id")
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId,
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const release = useQuery({
    queryKey: ["artifact-release", releaseId],
    queryFn: () => tenantApi.readArtifactRelease(releaseId),
    refetchInterval: 5000,
  })
  const retry = useMutation({
    mutationFn: tenantApi.retryArtifactDeployment,
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["artifact-release", releaseId],
      }),
  })
  const rollback = useMutation({
    mutationFn: tenantApi.rollbackArtifactDeployment,
    onSuccess: (next) =>
      navigate({
        to: "/system/runtimes/releases/$releaseId",
        params: { releaseId: next.id },
      }),
  })
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">内容发布详情</h1>
      <p className="text-sm text-muted-foreground">
        有效期至 {release.data?.valid_until}
      </p>
      {release.data?.deployments.map((deployment) => (
        <Card key={deployment.id}>
          <CardHeader>
            <CardTitle>节点 {deployment.node_id}</CardTitle>
          </CardHeader>
          <CardContent>
            <div>状态：{deployment.status}</div>
            <div>尝试次数：{deployment.attempt}</div>
            {deployment.error ? (
              <pre className="mt-2 text-xs text-destructive">
                {JSON.stringify(deployment.error, null, 2)}
              </pre>
            ) : null}
            {canManage ? (
              <div className="mt-4 flex gap-2">
                {["failed", "expired"].includes(deployment.status) ? (
                  <Button
                    disabled={retry.isPending}
                    onClick={() => retry.mutate(deployment.id)}
                    size="sm"
                    variant="outline"
                  >
                    重试部署
                  </Button>
                ) : null}
                {deployment.status === "applied" &&
                deployment.previous_artifact_id ? (
                  <Button
                    disabled={rollback.isPending}
                    onClick={() => {
                      if (window.confirm("确认回滚到节点已安装的上一版本？"))
                        rollback.mutate(deployment.id)
                    }}
                    size="sm"
                    variant="destructive"
                  >
                    回滚版本
                  </Button>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
