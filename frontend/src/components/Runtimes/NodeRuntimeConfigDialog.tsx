import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import { type RuntimeNode, tenantApi } from "@/api/tenantApi"
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

export default function NodeRuntimeConfigDialog({
  node,
}: {
  node: RuntimeNode
}) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<"platform_gateway" | "direct_anthropic">(
    "platform_gateway",
  )
  const [providerId, setProviderId] = useState("")
  const [modelId, setModelId] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const queryClient = useQueryClient()
  const providers = useQuery({
    queryKey: ["llm-provider-configs"],
    queryFn: tenantApi.readLlmProviderConfigs,
  })
  const provider = useMemo(
    () => providers.data?.data.find((item) => item.id === providerId),
    [providers.data, providerId],
  )
  const save = useMutation({
    mutationFn: () =>
      tenantApi.configureNodeRuntime(
        node.id,
        mode === "platform_gateway"
          ? {
              route_mode: mode,
              provider_config_id: providerId,
              model_id: modelId,
            }
          : {
              route_mode: mode,
              model_id: modelId,
              base_url: baseUrl,
              secret_inputs: { api_key: apiKey },
            },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["runtime-nodes"] })
      setOpen(false)
      setApiKey("")
    },
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          配置模型
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>配置 {node.name} 的运行时</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>连接方式</Label>
            <select
              className="mt-1 w-full rounded-md border bg-background p-2"
              value={mode}
              onChange={(event) => setMode(event.target.value as typeof mode)}
            >
              <option value="platform_gateway">平台 Gateway 转发</option>
              <option value="direct_anthropic">Anthropic 兼容 API 直连</option>
            </select>
          </div>
          {mode === "platform_gateway" ? (
            <>
              <div>
                <Label>平台模型配置</Label>
                <select
                  className="mt-1 w-full rounded-md border bg-background p-2"
                  value={providerId}
                  onChange={(event) => {
                    setProviderId(event.target.value)
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
              <div>
                <Label>模型</Label>
                <select
                  className="mt-1 w-full rounded-md border bg-background p-2"
                  value={modelId}
                  onChange={(event) => setModelId(event.target.value)}
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
              <div>
                <Label>API 地址</Label>
                <Input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                />
              </div>
              <div>
                <Label>模型 ID</Label>
                <Input
                  value={modelId}
                  onChange={(event) => setModelId(event.target.value)}
                />
              </div>
              <div>
                <Label>API Key</Label>
                <Input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                />
              </div>
            </>
          )}
          <Button
            className="w-full"
            disabled={
              !modelId ||
              save.isPending ||
              (mode === "direct_anthropic" && (!baseUrl || !apiKey))
            }
            onClick={() => save.mutate()}
          >
            保存并下发
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
