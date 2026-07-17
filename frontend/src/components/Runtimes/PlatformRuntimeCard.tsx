import { useQuery } from "@tanstack/react-query"

import { tenantApi } from "@/api/tenantApi"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import RuntimeConfigDialog from "./RuntimeConfigDialog"
import RuntimeSkillMatrix from "./RuntimeSkillMatrix"
import TestConversationSheet from "./TestConversationSheet"

export default function PlatformRuntimeCard({
  canManage,
}: {
  canManage: boolean
}) {
  const runtime = useQuery({
    queryKey: ["platform-runtime"],
    queryFn: tenantApi.readPlatformRuntime,
    retry: false,
  })
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle>平台运行时</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            由独立 Claude Agent SDK Worker 执行
          </p>
        </div>
        <div className="flex gap-2">
          {runtime.data ? (
            <TestConversationSheet runtimeId={runtime.data.id} />
          ) : null}
          {canManage ? <RuntimeConfigDialog /> : null}
        </div>
      </CardHeader>
      <CardContent>
        {runtime.data ? (
          <div className="grid gap-2 text-sm sm:grid-cols-4">
            <div>模型：{runtime.data.model_id}</div>
            <div>
              路由：
              {runtime.data.route_mode === "platform_gateway"
                ? "平台 Gateway"
                : "Anthropic 兼容直连"}
            </div>
            <div>权限模式：{runtime.data.permission_mode}</div>
            <div>
              兼容性：
              {runtime.data.compatibility_verified ? "已验证" : "未验证"}
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">尚未配置平台运行时。</p>
        )}
        {runtime.data ? (
          <div className="mt-5 border-t pt-4">
            <p className="mb-2 text-sm font-medium">Skill 同步状态</p>
            <RuntimeSkillMatrix runtimeId={runtime.data.id} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
