import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  modelDefinitionsApi,
  type RuntimeInstanceSummary,
  runtimeInstancesApi,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function RuntimeInstancePanel({
  canManage,
}: {
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const runtimes = useQuery({
    queryKey: ["runtime-instances"],
    queryFn: runtimeInstancesApi.list,
    refetchInterval: 5000,
  })
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [installationKey, setInstallationKey] = useState("")
  const [engineType, setEngineType] = useState<"claude_code" | "codex">(
    "claude_code",
  )
  const [executable, setExecutable] = useState("claude")
  const [selectedId, setSelectedId] = useState("")

  const create = useMutation({
    mutationFn: () =>
      runtimeInstancesApi.createPlatform({
        name,
        installation_key: installationKey,
        engine_type: engineType,
        adapter_version: "1.0.0",
        configuration: {
          executable,
          arguments: [],
          working_directory_policy: "workspace",
          environment_allowlist: [],
          security_policy: { permission_mode: "default" },
          resource_limits: {},
          expected_revision: 0,
        },
      }),
    onSuccess: () => {
      setOpen(false)
      showSuccessToast("Runtime 已创建，等待 Worker 应用配置并回报能力")
      void queryClient.invalidateQueries({ queryKey: ["runtime-instances"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle>Runtime 目录</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Runtime 是 Claude Code、Codex 等实际运行 Agent Loop
            的引擎实例；一台节点可拥有多个 Runtime。
          </p>
        </div>
        {canManage && (
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button>创建平台 Runtime</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>创建平台 Runtime</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>名称</Label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>安装标识</Label>
                  <Input
                    value={installationKey}
                    onChange={(e) => setInstallationKey(e.target.value)}
                    placeholder="platform-claude-primary"
                  />
                </div>
                <div className="space-y-2">
                  <Label>引擎</Label>
                  <Select
                    value={engineType}
                    onValueChange={(value: "claude_code" | "codex") => {
                      setEngineType(value)
                      setExecutable(value === "codex" ? "codex" : "claude")
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="claude_code">Claude Code</SelectItem>
                      <SelectItem value="codex">Codex</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>受控可执行文件</Label>
                  <Input
                    value={executable}
                    onChange={(e) => setExecutable(e.target.value)}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  onClick={() => create.mutate()}
                  disabled={
                    !name || !installationKey || !executable || create.isPending
                  }
                >
                  创建
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {runtimes.data?.data.map((runtime) => (
          <div key={runtime.id} className="rounded-lg border p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-medium">{runtime.name}</p>
                <p className="font-mono text-xs text-muted-foreground">
                  {runtime.id}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">{runtime.location_type}</Badge>
                <Badge variant="outline">{runtime.engine_type}</Badge>
                <Badge>{runtime.status}</Badge>
              </div>
            </div>
            <div className="mt-3 grid gap-2 text-xs text-muted-foreground md:grid-cols-4">
              <span>Engine {runtime.engine_version || "待发现"}</span>
              <span>Adapter {runtime.adapter_version}</span>
              <span>可用模型 {runtime.available_model_count}</span>
              <span>{runtime.enabled ? "已启用" : "未启用"}</span>
            </div>
            <Button
              className="mt-3"
              size="sm"
              variant="outline"
              onClick={() =>
                setSelectedId((current) =>
                  current === runtime.id ? "" : runtime.id,
                )
              }
            >
              {selectedId === runtime.id ? "收起配置" : "配置与模型能力"}
            </Button>
            {selectedId === runtime.id && (
              <RuntimeInstanceDetail runtime={runtime} canManage={canManage} />
            )}
          </div>
        ))}
        {runtimes.data?.data.length === 0 && (
          <p className="text-sm text-muted-foreground">尚未注册 Runtime。</p>
        )}
      </CardContent>
    </Card>
  )
}

function RuntimeInstanceDetail({
  runtime,
  canManage,
}: {
  runtime: RuntimeInstanceSummary
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const detail = useQuery({
    queryKey: ["runtime-instance", runtime.id],
    queryFn: () => runtimeInstancesApi.get(runtime.id),
    refetchInterval: 5000,
  })
  const bindings = useQuery({
    queryKey: ["runtime-model-bindings", runtime.id],
    queryFn: () => runtimeInstancesApi.listBindings(runtime.id),
    refetchInterval: 5000,
  })
  const definitions = useQuery({
    queryKey: ["model-definitions"],
    queryFn: modelDefinitionsApi.list,
  })
  const [loadedRuntimeId, setLoadedRuntimeId] = useState("")
  const [configExecutable, setConfigExecutable] = useState("")
  const [argumentsText, setArgumentsText] = useState("")
  const [environmentText, setEnvironmentText] = useState("")
  const [workingDirectoryPolicy, setWorkingDirectoryPolicy] =
    useState("workspace")
  const [modelDefinitionId, setModelDefinitionId] = useState("")
  const [engineModelId, setEngineModelId] = useState("")
  const [routeKey, setRouteKey] = useState("native")

  useEffect(() => {
    if (!detail.data || loadedRuntimeId === runtime.id) return
    const latest = detail.data.configurations[0]
    setLoadedRuntimeId(runtime.id)
    setConfigExecutable(
      latest?.executable ||
        (runtime.engine_type === "codex" ? "codex" : "claude"),
    )
    setArgumentsText(latest?.arguments.join("\n") || "")
    setEnvironmentText(latest?.environment_allowlist.join("\n") || "")
    setWorkingDirectoryPolicy(latest?.working_directory_policy || "workspace")
  }, [detail.data, loadedRuntimeId, runtime.engine_type, runtime.id])

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["runtime-instances"] }),
      queryClient.invalidateQueries({
        queryKey: ["runtime-instance", runtime.id],
      }),
      queryClient.invalidateQueries({
        queryKey: ["runtime-model-bindings", runtime.id],
      }),
    ])
  }
  const saveConfiguration = useMutation({
    mutationFn: async () => {
      const created = (await runtimeInstancesApi.saveConfiguration(runtime.id, {
        expected_revision: detail.data?.configurations[0]?.revision || 0,
        executable: configExecutable,
        arguments: lines(argumentsText),
        working_directory_policy: workingDirectoryPolicy,
        environment_allowlist: lines(environmentText),
        security_policy: { permission_mode: "default" },
        resource_limits: {},
      })) as { id: string }
      return runtimeInstancesApi.applyConfiguration(runtime.id, created.id)
    },
    onSuccess: async () => {
      showSuccessToast("Runtime 配置修订已提交应用")
      setLoadedRuntimeId("")
      await refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const enable = useMutation({
    mutationFn: () =>
      runtimeInstancesApi.enableNode(runtime.runtime_node_id || "", runtime.id),
    onSuccess: async () => {
      showSuccessToast("Node Runtime 已启用")
      await refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const createBinding = useMutation({
    mutationFn: () =>
      runtimeInstancesApi.createBinding(runtime.id, {
        model_definition_id: modelDefinitionId,
        route_type: "runtime_native",
        route_key: routeKey,
        engine_model_id: engineModelId,
      }),
    onSuccess: async () => {
      setEngineModelId("")
      showSuccessToast("模型绑定已声明，请执行真实校验")
      await refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const bindingAction = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string
      action: "validate" | "disable"
    }) =>
      action === "validate"
        ? runtimeInstancesApi.validateBinding(runtime.id, id)
        : runtimeInstancesApi.disableBinding(runtime.id, id),
    onSuccess: refresh,
    onError: handleError.bind(showErrorToast),
  })
  const latestReport = detail.data?.capability_reports[0]

  return (
    <div className="mt-4 space-y-4 border-t pt-4">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded border p-3">
          <p className="font-medium">当前能力证据</p>
          <pre className="mt-2 max-h-48 overflow-auto text-xs">
            {JSON.stringify(latestReport || { status: "尚未上报" }, null, 2)}
          </pre>
        </div>
        <div className="space-y-2 rounded border p-3">
          <p className="font-medium">配置修订</p>
          <Input
            value={configExecutable}
            onChange={(event) => setConfigExecutable(event.target.value)}
            placeholder="受控可执行文件"
            disabled={!canManage}
          />
          <Input
            value={argumentsText}
            onChange={(event) => setArgumentsText(event.target.value)}
            placeholder="参数，每行一个"
            disabled={!canManage}
          />
          <Input
            value={environmentText}
            onChange={(event) => setEnvironmentText(event.target.value)}
            placeholder="环境变量白名单，每行一个"
            disabled={!canManage}
          />
          <Select
            value={workingDirectoryPolicy}
            onValueChange={setWorkingDirectoryPolicy}
            disabled={!canManage}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="workspace">workspace</SelectItem>
              <SelectItem value="project">project</SelectItem>
              <SelectItem value="isolated">isolated</SelectItem>
            </SelectContent>
          </Select>
          {canManage && (
            <div className="flex gap-2">
              <Button
                size="sm"
                disabled={!configExecutable || saveConfiguration.isPending}
                onClick={() => saveConfiguration.mutate()}
              >
                保存并应用新修订
              </Button>
              {runtime.location_type === "node" && !runtime.enabled && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    !detail.data?.configurations.length || enable.isPending
                  }
                  onClick={() => enable.mutate()}
                >
                  启用实例
                </Button>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="space-y-3 rounded border p-3">
        <p className="font-medium">受支持模型（RuntimeModelBinding）</p>
        {canManage && (
          <div className="grid gap-2 md:grid-cols-4">
            <Select
              value={modelDefinitionId}
              onValueChange={setModelDefinitionId}
            >
              <SelectTrigger>
                <SelectValue placeholder="稳定模型身份" />
              </SelectTrigger>
              <SelectContent>
                {definitions.data?.data
                  .filter((definition) => definition.enabled)
                  .map((definition) => (
                    <SelectItem key={definition.id} value={definition.id}>
                      {definition.display_name || definition.model_key}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <Input
              value={engineModelId}
              onChange={(event) => setEngineModelId(event.target.value)}
              placeholder="引擎模型 ID"
            />
            <Input
              value={routeKey}
              onChange={(event) => setRouteKey(event.target.value)}
              placeholder="路由键"
            />
            <Button
              disabled={
                !modelDefinitionId ||
                !engineModelId ||
                !routeKey ||
                createBinding.isPending
              }
              onClick={() => createBinding.mutate()}
            >
              声明绑定
            </Button>
          </div>
        )}
        {bindings.data?.data.map((binding) => (
          <div
            key={binding.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded bg-muted/40 p-2 text-sm"
          >
            <div>
              <p>{binding.engine_model_id}</p>
              <p className="text-xs text-muted-foreground">
                {binding.model_definition_id} · {binding.route_type}:
                {binding.route_key}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Badge>{binding.status}</Badge>
              {canManage && binding.status !== "disabled" && (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={bindingAction.isPending}
                    onClick={() =>
                      bindingAction.mutate({
                        id: binding.id,
                        action: "validate",
                      })
                    }
                  >
                    真实校验
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={bindingAction.isPending}
                    onClick={() =>
                      bindingAction.mutate({
                        id: binding.id,
                        action: "disable",
                      })
                    }
                  >
                    停用
                  </Button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function lines(value: string) {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean)
}
