import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  type EnvironmentCatalog,
  type HarnessProfilePublic,
  harnessCatalogApi,
  harnessProfilesApi,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
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
  const [workingDirectoryStrategy, setWorkingDirectoryStrategy] =
    useState("inherit")
  const [allowedTools, setAllowedTools] = useState("")
  const [disallowedTools, setDisallowedTools] = useState("")

  useEffect(() => {
    if (profile) {
      setName(profile.name)
      setCliConstraint(profile.cli_version_constraint)
      setSdkConstraint(profile.sdk_version_constraint)
      setPermissionMode(
        ((profile.config as Record<string, unknown>)
          .permission_mode as string) ?? "default",
      )
      setTimeoutSeconds(
        ((profile.config as Record<string, unknown>)
          .timeout_seconds as number) ?? 3600,
      )
      setAllowedEnv(
        ((profile.config as Record<string, unknown>)
          .allowed_env_names as string[]) ?? [],
      )
      setWorkingDirectoryStrategy(
        ((profile.config as Record<string, unknown>)
          .working_directory_strategy as string) ?? "inherit",
      )
      setAllowedTools(
        (
          ((profile.config as Record<string, unknown>)
            .allowed_tools as string[]) ?? []
        ).join(", "),
      )
      setDisallowedTools(
        (
          ((profile.config as Record<string, unknown>)
            .disallowed_tools as string[]) ?? []
        ).join(", "),
      )
    } else {
      setName("")
      setCliConstraint(">=1.0.0")
      setSdkConstraint(">=0.2.0")
      setPermissionMode("default")
      setTimeoutSeconds(3600)
      setAllowedEnv([])
      setWorkingDirectoryStrategy("inherit")
      setAllowedTools("")
      setDisallowedTools("")
    }
  }, [profile])

  const buildConfig = () => ({
    permission_mode: permissionMode,
    timeout_seconds: timeoutSeconds,
    allowed_env_names: allowedEnv,
    working_directory_strategy: workingDirectoryStrategy,
    allowed_tools: allowedTools
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
    disallowed_tools: disallowedTools
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
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

  const archiveMutation = useMutation({
    mutationFn: () =>
      harnessProfilesApi.update(profile!.id, { archived: !profile!.archived }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["harness-profiles"] })
      onOpenChange(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteMutation = useMutation({
    mutationFn: () => harnessProfilesApi.delete(profile!.id),
    onSuccess: () => {
      showSuccessToast("Profile 已删除")
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
            <Label>工作目录策略</Label>
            <Select
              value={workingDirectoryStrategy}
              onValueChange={setWorkingDirectoryStrategy}
              disabled={!canManage || Boolean(profile?.archived)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inherit">继承运行时工作区</SelectItem>
                <SelectItem value="require_root">要求预登记根目录</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>允许的内置工具</Label>
            <Input
              value={allowedTools}
              onChange={(event) => setAllowedTools(event.target.value)}
              placeholder="Read, Glob, Grep"
              disabled={!canManage || Boolean(profile?.archived)}
            />
          </div>
          <div className="space-y-2">
            <Label>禁用的内置工具</Label>
            <Input
              value={disallowedTools}
              onChange={(event) => setDisallowedTools(event.target.value)}
              placeholder="Bash, WebFetch"
              disabled={!canManage || Boolean(profile?.archived)}
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
                <button
                  type="button"
                  key={e}
                  className="rounded bg-gray-100 px-2 py-0.5 text-xs"
                  disabled={!canManage}
                  onClick={() =>
                    setAllowedEnv(allowedEnv.filter((x) => x !== e))
                  }
                >
                  {e} ✕
                </button>
              ))}
            </div>
          </div>
        </div>
        <SheetFooter>
          {canManage && !profile?.archived && (
            <Button
              onClick={() =>
                profile ? updateMutation.mutate() : createMutation.mutate()
              }
              disabled={
                !name || createMutation.isPending || updateMutation.isPending
              }
            >
              {profile ? "保存" : "创建"}
            </Button>
          )}
          {canManage && profile && (
            <Button
              variant="outline"
              onClick={() => archiveMutation.mutate()}
              disabled={archiveMutation.isPending}
            >
              {profile.archived ? "恢复 Profile" : "归档 Profile"}
            </Button>
          )}
          {canManage && profile && !profile.referenced_by_agents && (
            <Button
              variant="destructive"
              onClick={() => {
                if (
                  window.confirm(
                    "确认删除这个未被引用的 Profile？此操作不可撤销。",
                  )
                ) {
                  deleteMutation.mutate()
                }
              }}
              disabled={deleteMutation.isPending}
            >
              删除 Profile
            </Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
