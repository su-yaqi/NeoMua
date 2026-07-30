import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import { tenantApi } from "@/api/tenantApi"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import ArtifactReleaseDialog from "./ArtifactReleaseDialog"
import ArtifactUploadDialog from "./ArtifactUploadDialog"

export default function ArtifactPanel({ canManage }: { canManage: boolean }) {
  const artifacts = useQuery({
    queryKey: ["runtime-artifacts"],
    queryFn: tenantApi.readRuntimeArtifacts,
  })
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>运行时内容分发</CardTitle>
        {canManage ? <ArtifactUploadDialog /> : null}
      </CardHeader>
      <CardContent>
        {artifacts.data?.data.length ? (
          <div className="space-y-2">
            {artifacts.data.data.map((artifact) => (
              <div
                className="flex items-center gap-3 rounded-md border p-3"
                key={artifact.id}
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium">
                    {artifact.kind} · {artifact.version}
                  </div>
                  <div className="truncate font-mono text-xs text-muted-foreground">
                    sha256:{artifact.content_sha256}
                  </div>
                </div>
                {canManage ? (
                  <ArtifactReleaseDialog artifact={artifact} />
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无已签名内容包。</p>
        )}
        <Link
          className="mt-3 inline-block text-sm underline"
          to="/system/runtimes/artifacts"
        >
          查看全部内容
        </Link>
      </CardContent>
    </Card>
  )
}
