import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { harnessProfilesApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const Route = createFileRoute("/_layout/system/harnesses")({
  component: HarnessHistoryPage,
})

function HarnessHistoryPage() {
  const profiles = useQuery({
    queryKey: ["harness-profiles-history"],
    queryFn: harnessProfilesApi.list,
  })
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Harness 历史配置</h1>
        <p className="text-muted-foreground">
          v0.9 起 Harness 已归入 Runtime
          引擎适配层；以下记录仅用于历史审计，不再允许创建或修改。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>只读记录</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {profiles.data?.data.map((profile) => (
            <div
              key={profile.id}
              className="flex items-center justify-between rounded-md border p-3"
            >
              <div>
                <p className="font-medium">{profile.name}</p>
                <p className="font-mono text-xs text-muted-foreground">
                  {profile.id}
                </p>
              </div>
              <Badge variant="outline">{profile.harness_type}</Badge>
            </div>
          ))}
          {profiles.data?.data.length === 0 && (
            <p className="text-sm text-muted-foreground">
              没有历史 Harness 配置。
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
