import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toolsApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function ToolPolicyManager({
  canManage,
}: {
  canManage: boolean
}) {
  const client = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { data } = useQuery({ queryKey: ["tools"], queryFn: toolsApi.list })
  const mutation = useMutation({
    mutationFn: ({ key, policy }: { key: string; policy: string }) =>
      toolsApi.setPolicy(key, policy),
    onSuccess: () => {
      showSuccessToast("Tool 策略已更新")
      client.invalidateQueries({ queryKey: ["tools"] })
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Tool 策略</h1>
        <p className="text-muted-foreground">
          平台基线优先；空间策略只能禁用或提高审批要求。
        </p>
      </div>
      {data?.data.map((raw) => {
        const key = String(raw.tool_key)
        return (
          <Card key={key}>
            <CardContent className="flex items-center justify-between gap-4 pt-6">
              <div>
                <p className="font-medium">{String(raw.display_name)}</p>
                <p className="text-sm text-muted-foreground">
                  {key} · {String(raw.source)} · {String(raw.risk_level)} · 基线{" "}
                  {String(raw.baseline_policy)}
                </p>
              </div>
              {canManage && raw.source === "builtin" && (
                <div className="flex gap-2">
                  {(["inherit", "require_approval", "disabled"] as const).map(
                    (policy) => (
                      <Button
                        key={policy}
                        size="sm"
                        variant={
                          raw.namespace_policy === policy
                            ? "default"
                            : "outline"
                        }
                        onClick={() => mutation.mutate({ key, policy })}
                      >
                        {policy}
                      </Button>
                    ),
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
