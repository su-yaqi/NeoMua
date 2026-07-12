import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  harnessProfilesApi,
  harnessCatalogApi,
  type HarnessProfilePublic,
  type EnvironmentCatalog,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Sheet,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function HarnessProfileSheet({
  profile,
  open,
  onOpenChange,
  canManage,
}: {
  profile: HarnessProfilePublic | null
  open: boolean
  onOpenChange: (open: boolean) => void
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { data: envCatalog } = useQuery<EnvironmentCatalog>({
    queryKey: ["env-catalog"],
    queryFn: harnessCatalogApi.environment,
  })

  const [name, setName] = useState("")
  const [cliConstraint, setCliConstraint] = useState(">=1.0.0")
  const [sdkConstraint, setSdkConstraint] = useState(">=0.2.0")
  const [permissionMode, setPermissionMode] = useState("default")
  const [timeoutSeconds, setTimeoutSeconds] = useState(3600)
  const [allowedEnv, setAllowedEnv] = useState<string[]>([])

  useEffect(() => {
    if (profile) {
      setName(profile.name)
      setCliConstraint(profile.cli_version_constraint)
      setSdkConstraint(profile.sdk_version_constraint)
      setPermissionMode(
        (profile.config as Record<string, unknown>).permission_mode as string ??
          "default"
      )
      setTimeoutSeconds(
        (profile.config as Record<string, unknown>).timeout_seconds as number ??
          3600
      )
      setAllowedEnv(
        ((profile.config as Record<string, unknown>).allowed_env_names as string[]) ??
          []
      )
    } else {
      setName("")
      setCliConstraint(">=1.0.0")
      setSdkConstraint(">=0.2.0")
      setPermissionMode("default")
      setTimeoutSeconds(3600)
      setAllowedEnv([])
    }
  }, [profile])

  const buildConfig = () => ({
    permission_mode: permissionMode,
    timeout_seconds: timeoutSeconds,
    allowed_env_names: allowedEnv,
  })

  const createMutation = useMutation({
    mutationFn: () =>
      harnessProfilesApi.create({
        name,
        cli_version_constraint: cliConstraint,
        sdk_version_constraint: sdkConstraint,
        config: buildConfig(),
      }),
    onSuccess: () => {
      showSuccessToast("Profile 已创建")
      queryClient.invalidateQueries({ queryKey: ["harness-profiles"] })
      onOpenChange(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  const updateMutation = useMutation({
    mutationFn: () =>
      harnessProfilesApi.update(profile!.id, {
        name,
        cli_version_constraint: cliConstraint,
        sdk_version_constraint: sdkConstraint,
        config: buildConfig(),
      }),
    onSuccess: () => {
      showSuccessToast("Profile 已更新")
      queryClient.invalidateQueries({ queryKey: ["harness-profiles"] })
      onOpenChange(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{profile ? "编辑 Profile" : "创建 Profile"}</SheetTitle>
        </SheetHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>名称</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>Harness 类型</Label>
            <Input value="claude_code" disabled />
            <p className="text-xs text-muted-foreground">
              v0.5 仅支持 claude_code。
            </p>
          </div>
          <div className="space-y-2">
            <Label>CLI 版本约束</Label>
            <Input
              value={cliConstraint}
              onChange={(e) => setCliConstraint(e.target.value)}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>SDK 版本约束</Label>
            <Input
              value={sdkConstraint}
              onChange={(e) => setSdkConstraint(e.target.value)}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>权限模式</Label>
            <Select
              value={permissionMode}
              onValueChange={setPermissionMode}
              disabled={!canManage}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">default</SelectItem>
                <SelectItem value="acceptEdits">acceptEdits</SelectItem>
                <SelectItem value="plan">plan</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              bypassPermissions 不可选，由后端强制拒绝。
            </p>
          </div>
          <div className="space-y-2">
            <Label>超时（秒）</Label>
            <Input
              type="number"
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>允许的环境变量</Label>
            <Select
              value=""
              onValueChange={(v) => {
                if (!allowedEnv.includes(v)) setAllowedEnv([...allowedEnv, v])
              }}
              disabled={!canManage}
            >
              <SelectTrigger>
                <SelectValue placeholder="从 allowlist 选择" />
              </SelectTrigger>
              <SelectContent>
                {envCatalog?.allowlist
                  .filter((e) => !allowedEnv.includes(e))
                  .map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <div className="flex flex-wrap gap-1">
              {allowedEnv.map((e) => (
                <span
                  key={e}
                  className="rounded bg-gray-100 px-2 py-0.5 text-xs"
                  onClick={() =>
                    canManage && setAllowedEnv(allowedEnv.filter((x) => x !== e))
                  }
                >
                  {e} ✕
                </span>
              ))}
            </div>
          </div>
        </div>
        <SheetFooter>
          {canManage && (
            <Button
              onClick={() =>
                profile ? updateMutation.mutate() : createMutation.mutate()
              }
              disabled={!name || createMutation.isPending || updateMutation.isPending}
            >
              {profile ? "保存" : "创建"}
            </Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
