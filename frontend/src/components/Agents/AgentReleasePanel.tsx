import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { releasesApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function AgentReleasePanel({
  agentId,
  revision,
  validatedRevision,
  canManage,
}: {
  agentId: string
  revision: number
  validatedRevision: number | null
  canManage: boolean
}) {
  const client = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [version, setVersion] = useState("")
  const { data } = useQuery({
    queryKey: ["agent-releases", agentId],
    queryFn: () => releasesApi.list(agentId),
  })
  const create = useMutation({
    mutationFn: () =>
      releasesApi.create(agentId, { draft_revision: revision, version }),
    onSuccess: () => {
      showSuccessToast("不可变 Agent Release 已创建")
      setVersion("")
      client.invalidateQueries({ queryKey: ["agent-releases", agentId] })
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>发布 revision {revision}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            仅当前 revision 完整校验通过后可发布；Release 固化同一
            ResolvedAgentSpec digest。
          </p>
          {canManage && (
            <div className="flex gap-2">
              <Input
                placeholder="1.0.0"
                value={version}
                onChange={(event) => setVersion(event.target.value)}
              />
              <Button
                disabled={
                  validatedRevision !== revision || !version || create.isPending
                }
                onClick={() => create.mutate()}
              >
                创建 Release
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
      {data?.data.map((release) => (
        <Card
          key={release.id}
          className="cursor-pointer"
          onClick={() =>
            window.location.assign(
              `/system/agents/${agentId}/releases/${release.id}`,
            )
          }
        >
          <CardContent className="pt-6">
            <p className="font-medium">v{release.version}</p>
            <code className="text-xs">{release.resolved_spec_digest}</code>
            <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">
              {JSON.stringify(release.dependency_lock, null, 2)}
            </pre>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
