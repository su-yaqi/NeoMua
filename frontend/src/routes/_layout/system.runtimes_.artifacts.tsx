import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"

import { tenantApi } from "@/api/tenantApi"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const Route = createFileRoute("/_layout/system/runtimes_/artifacts")({
  component: RuntimeArtifactsPage,
})

function RuntimeArtifactsPage() {
  const artifacts = useQuery({
    queryKey: ["runtime-artifacts"],
    queryFn: tenantApi.readRuntimeArtifacts,
  })
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">已签名运行时内容</h1>
      {artifacts.data?.data.map((artifact) => (
        <Card key={artifact.id}>
          <CardHeader>
            <CardTitle>
              {artifact.kind} · {artifact.version}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="font-mono text-xs">
              sha256:{artifact.content_sha256}
            </div>
            <details className="mt-3">
              <summary>签名清单</summary>
              <pre className="mt-2 overflow-auto text-xs">
                {JSON.stringify(artifact.manifest, null, 2)}
              </pre>
            </details>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
