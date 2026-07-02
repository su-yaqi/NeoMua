import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"

import { tenantApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export default function RuntimeConfigDialog() {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<"platform_gateway" | "direct_anthropic">(
    "platform_gateway",
  )
  const [providerId, setProviderId] = useState("")
  const [modelId, setModelId] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const queryClient = useQueryClient()
  const runtime = useQuery({
    queryKey: ["platform-runtime"],
    queryFn: tenantApi.readPlatformRuntime,
    retry: false,
  })
  const providers = useQuery({
    queryKey: ["llm-provider-configs"],
    queryFn: tenantApi.readLlmProviderConfigs,
  })
  const provider = useMemo(
    () => providers.data?.data.find((item) => item.id === providerId),
    [providers.data, providerId],
  )

  useEffect(() => {
    if (!runtime.data) return
    setMode(runtime.data.route_mode)
    setProviderId(runtime.data.provider_config_id ?? "")
    setModelId(runtime.data.model_id)
    setBaseUrl(runtime.data.base_url ?? "")
  }, [runtime.data])

  const save = useMutation({
    mutationFn: () =>
      tenantApi.upsertPlatformRuntime(
        mode === "platform_gateway"
          ? {
              route_mode: mode,
              provider_config_id: providerId,
              model_id: modelId,
              permission_mode: "default",
            }
          : {
              route_mode: mode,
              model_id: modelId,
              base_url: baseUrl,
              permission_mode: "default",
              secret_inputs: { api_key: apiKey },
            },
      ),
    onSuccess: async () => {
      await tenantApi.validatePlatformRuntime()
      await queryClient.invalidateQueries({ queryKey: ["platform-runtime"] })
      setApiKey("")
      setOpen(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>配置</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>配置平台运行时</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>模型连接方式</Label>
            <select
              className="w-full rounded-md border bg-background p-2"
              value={mode}
              onChange={(e) => setMode(e.target.value as typeof mode)}
            >
              <option value="platform_gateway">平台 Gateway 转发</option>
              <option value="direct_anthropic">Anthropic 兼容 API 直连</option>
            </select>
          </div>
          {mode === "platform_gateway" ? (
            <>
              <div className="space-y-2">
                <Label>平台模型配置</Label>
                <select
                  className="w-full rounded-md border bg-background p-2"
                  value={providerId}
                  onChange={(e) => {
                    setProviderId(e.target.value)
                    setModelId("")
                  }}
                >
                  <option value="">请选择</option>
                  {providers.data?.data
                    .filter((item) => item.enabled)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.config_name}
                      </option>
                    ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label>模型</Label>
                <select
                  className="w-full rounded-md border bg-background p-2"
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                >
                  <option value="">请选择</option>
                  {provider?.models
                    .filter((item) => item.is_enabled)
                    .map((item) => (
                      <option key={item.id} value={item.model_id}>
                        {item.display_name || item.model_id}
                      </option>
                    ))}
                </select>
              </div>
            </>
          ) : (
            <>
              <div className="space-y-2">
                <Label>Anthropic 兼容 API 地址</Label>
                <Input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>模型 ID</Label>
                <Input
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>API Key</Label>
                <Input
                  type="password"
                  value={apiKey}
                  placeholder={runtime.data?.secret_masked ?? ""}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </div>
            </>
          )}
          {save.isError ? (
            <p className="text-sm text-destructive">{save.error.message}</p>
          ) : null}
          <Button
            className="w-full"
            disabled={
              !modelId ||
              save.isPending ||
              (mode === "direct_anthropic" && (!baseUrl || !apiKey))
            }
            onClick={() => save.mutate()}
          >
            保存
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
