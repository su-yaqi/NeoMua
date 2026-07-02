import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Plus, RefreshCw, ShieldCheck } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { z } from "zod"

import {
  type LlmProviderCatalogItem,
  type LlmProviderConfig,
  type LlmProviderModel,
  tenantApi,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

type EditableModel = {
  model_id: string
  display_name: string
  source_type: "discovered" | "manual"
  is_enabled: boolean
  sync_status: "active" | "stale" | "sync_failed"
}

interface ProviderConfigDialogProps {
  catalog: LlmProviderCatalogItem[]
  config?: LlmProviderConfig | null
  triggerLabel?: string
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

const statusLabel: Record<string, string> = {
  unverified: "未校验",
  success: "校验成功",
  failed: "校验失败",
  unsupported: "不支持校验",
}

function normalizeModels(models: LlmProviderModel[] = []): EditableModel[] {
  return models.map((item) => ({
    model_id: item.model_id,
    display_name: item.display_name ?? item.model_id,
    source_type: item.source_type,
    is_enabled: item.is_enabled,
    sync_status: item.sync_status,
  }))
}

function ProviderConfigDialog({
  catalog,
  config,
  triggerLabel = "新增配置",
  open: controlledOpen,
  onOpenChange,
}: ProviderConfigDialogProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false)
  const [providerSlug, setProviderSlug] = useState(config?.provider_slug ?? "")
  const [configName, setConfigName] = useState(config?.config_name ?? "")
  const [baseUrl, setBaseUrl] = useState(config?.base_url ?? "")
  const [enabled, setEnabled] = useState(config?.enabled ?? true)
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({})
  const [extraConfig, setExtraConfig] = useState<Record<string, string>>({})
  const [models, setModels] = useState<EditableModel[]>(
    normalizeModels(config?.models),
  )
  const [manualModelId, setManualModelId] = useState("")
  const [manualModelName, setManualModelName] = useState("")
  const [workingConfig, setWorkingConfig] = useState<LlmProviderConfig | null>(
    config ?? null,
  )
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const isOpen = controlledOpen ?? uncontrolledOpen
  const setIsOpen = onOpenChange ?? setUncontrolledOpen

  const selectedProvider = useMemo(
    () => catalog.find((item) => item.provider_slug === providerSlug) ?? null,
    [catalog, providerSlug],
  )
  const baseUrlDisabled =
    selectedProvider !== null &&
    !selectedProvider.base_url_editable &&
    Boolean(selectedProvider.default_base_url)

  useEffect(() => {
    if (!isOpen) {
      return
    }
    setWorkingConfig(config ?? null)
    setProviderSlug(config?.provider_slug ?? catalog[0]?.provider_slug ?? "")
    setConfigName(config?.config_name ?? "")
    setBaseUrl(config?.base_url ?? "")
    setEnabled(config?.enabled ?? true)
    setSecretInputs({})
    setExtraConfig((config?.extra_config as Record<string, string>) ?? {})
    setModels(normalizeModels(config?.models))
    setManualModelId("")
    setManualModelName("")
  }, [catalog, config, isOpen])

  useEffect(() => {
    if (!selectedProvider || config) {
      return
    }
    if (selectedProvider.default_base_url) {
      setBaseUrl(selectedProvider.default_base_url)
    }
  }, [config, selectedProvider])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const parsedName = z.string().min(1).parse(configName)
      const parsedBaseUrl = z.string().min(1).parse(baseUrl)
      const manualModels = models
        .filter((item) => item.source_type === "manual")
        .map((item) => ({
          model_id: item.model_id,
          display_name: item.display_name,
        }))
      const enabledModelIds = models
        .filter((item) => item.is_enabled)
        .map((item) => item.model_id)
      const sanitizedSecretInputs = Object.fromEntries(
        Object.entries(secretInputs).filter(
          ([, value]) => value.trim().length > 0,
        ),
      )

      if (workingConfig) {
        return tenantApi.updateLlmProviderConfig(workingConfig.id, {
          config_name: parsedName,
          base_url: parsedBaseUrl,
          enabled,
          extra_config: extraConfig,
          manual_models: manualModels,
          enabled_model_ids: enabledModelIds,
          ...(Object.keys(sanitizedSecretInputs).length > 0
            ? { secret_inputs: sanitizedSecretInputs }
            : {}),
        })
      }

      if (!providerSlug) {
        throw new Error("请选择供应商")
      }

      return tenantApi.createLlmProviderConfig({
        config_name: parsedName,
        provider_slug: providerSlug,
        base_url: parsedBaseUrl,
        enabled,
        secret_inputs: sanitizedSecretInputs,
        extra_config: extraConfig,
        manual_models: manualModels,
        enabled_model_ids: enabledModelIds,
      })
    },
    onSuccess: (savedConfig) => {
      showSuccessToast(workingConfig ? "配置已更新" : "配置已创建")
      setWorkingConfig(savedConfig)
      setModels(normalizeModels(savedConfig.models))
      setSecretInputs({})
      queryClient.invalidateQueries({ queryKey: ["llm-provider-configs"] })
      setIsOpen(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  const validateMutation = useMutation({
    mutationFn: async () => {
      if (!workingConfig) {
        throw new Error("请先保存配置，再执行校验")
      }
      return tenantApi.validateLlmProviderConfig(workingConfig.id)
    },
    onSuccess: (nextConfig) => {
      setWorkingConfig(nextConfig)
      setModels(normalizeModels(nextConfig.models))
      queryClient.invalidateQueries({ queryKey: ["llm-provider-configs"] })
      showSuccessToast(nextConfig.validation_message ?? "连接校验已完成")
    },
    onError: handleError.bind(showErrorToast),
  })

  const syncMutation = useMutation({
    mutationFn: async () => {
      if (!workingConfig) {
        throw new Error("请先保存配置，再同步模型")
      }
      return tenantApi.syncLlmProviderConfigModels(workingConfig.id, {
        manual_models: models
          .filter((item) => item.source_type === "manual")
          .map((item) => ({
            model_id: item.model_id,
            display_name: item.display_name,
          })),
        enabled_model_ids: models
          .filter((item) => item.is_enabled)
          .map((item) => item.model_id),
      })
    },
    onSuccess: (nextConfig) => {
      setWorkingConfig(nextConfig)
      setModels(normalizeModels(nextConfig.models))
      queryClient.invalidateQueries({ queryKey: ["llm-provider-configs"] })
      showSuccessToast(nextConfig.validation_message ?? "模型同步已完成")
    },
    onError: handleError.bind(showErrorToast),
  })

  const addManualModel = () => {
    if (!manualModelId.trim()) {
      showErrorToast("请输入模型 ID")
      return
    }
    setModels((current) => {
      const rest = current.filter(
        (item) => item.model_id !== manualModelId.trim(),
      )
      const nextModels: EditableModel[] = [
        ...rest,
        {
          model_id: manualModelId.trim(),
          display_name: manualModelName.trim() || manualModelId.trim(),
          source_type: "manual",
          is_enabled: true,
          sync_status: "active",
        },
      ]
      return nextModels.sort((left, right) =>
        left.model_id.localeCompare(right.model_id),
      )
    })
    setManualModelId("")
    setManualModelName("")
  }

  const removeManualModel = (modelId: string) => {
    setModels((current) =>
      current.filter(
        (item) => !(item.source_type === "manual" && item.model_id === modelId),
      ),
    )
  }

  const toggleModelEnabled = (modelId: string, nextValue: boolean) => {
    setModels((current) =>
      current.map((item) =>
        item.model_id === modelId ? { ...item, is_enabled: nextValue } : item,
      ),
    )
  }

  const trigger = onOpenChange ? null : (
    <DialogTrigger asChild>
      <Button>
        <Plus className="mr-2 size-4" />
        {triggerLabel}
      </Button>
    </DialogTrigger>
  )

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      {trigger}
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            {workingConfig ? "编辑大模型接入配置" : "新增大模型接入配置"}
          </DialogTitle>
          <DialogDescription>
            维护当前空间的大模型供应商接入参数、密钥和可用模型。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-6">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="config-name">配置名称</Label>
              <Input
                id="config-name"
                value={configName}
                onChange={(event) => setConfigName(event.target.value)}
                placeholder="例如：DeepSeek 生产"
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="provider-slug">供应商</Label>
              {workingConfig ? (
                <Input
                  id="provider-slug"
                  value={workingConfig.provider_display_name}
                  disabled
                />
              ) : (
                <Select value={providerSlug} onValueChange={setProviderSlug}>
                  <SelectTrigger id="provider-slug">
                    <SelectValue placeholder="请选择供应商" />
                  </SelectTrigger>
                  <SelectContent>
                    {catalog.map((item) => (
                      <SelectItem
                        key={item.provider_slug}
                        value={item.provider_slug}
                      >
                        {item.display_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            <div className="grid gap-2 md:col-span-2">
              <Label htmlFor="base-url">接入地址</Label>
              <Input
                id="base-url"
                value={baseUrl}
                disabled={baseUrlDisabled}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.example.com/v1"
              />
              {selectedProvider?.description ? (
                <p className="text-sm text-muted-foreground">
                  {selectedProvider.description}
                </p>
              ) : null}
            </div>
          </div>

          {selectedProvider ? (
            <div className="grid gap-4 md:grid-cols-2">
              {selectedProvider.secret_fields.map((field) => (
                <div className="grid gap-2" key={field.name}>
                  <Label htmlFor={field.name}>
                    {field.label}
                    {field.required ? " *" : ""}
                  </Label>
                  <Input
                    id={field.name}
                    type="password"
                    value={secretInputs[field.name] ?? ""}
                    onChange={(event) =>
                      setSecretInputs((current) => ({
                        ...current,
                        [field.name]: event.target.value,
                      }))
                    }
                    placeholder={
                      workingConfig?.secret_masked
                        ? `${workingConfig.secret_masked}（留空则保持不变）`
                        : (field.placeholder ?? undefined)
                    }
                  />
                  {field.help_text ? (
                    <p className="text-sm text-muted-foreground">
                      {field.help_text}
                    </p>
                  ) : null}
                </div>
              ))}

              {selectedProvider.extra_fields.map((field) => (
                <div className="grid gap-2" key={field.name}>
                  <Label htmlFor={field.name}>
                    {field.label}
                    {field.required ? " *" : ""}
                  </Label>
                  <Input
                    id={field.name}
                    value={extraConfig[field.name] ?? ""}
                    onChange={(event) =>
                      setExtraConfig((current) => ({
                        ...current,
                        [field.name]: event.target.value,
                      }))
                    }
                    placeholder={field.placeholder ?? undefined}
                  />
                </div>
              ))}
            </div>
          ) : null}

          <div className="flex items-center gap-3">
            <Checkbox
              checked={enabled}
              onCheckedChange={(checked) => setEnabled(checked === true)}
              id="config-enabled"
            />
            <Label htmlFor="config-enabled">启用该配置</Label>
            {workingConfig ? (
              <Badge variant="outline">
                {statusLabel[workingConfig.validation_status] ??
                  workingConfig.validation_status}
              </Badge>
            ) : null}
          </div>

          <div className="grid gap-4 rounded-lg border p-4">
            <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div>
                <h3 className="font-semibold">模型管理</h3>
                <p className="text-sm text-muted-foreground">
                  支持自动同步供应商模型，也支持手工补录模型 ID。
                </p>
              </div>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={!workingConfig || validateMutation.isPending}
                  onClick={() => validateMutation.mutate()}
                >
                  <ShieldCheck className="mr-2 size-4" />
                  校验连接
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={!workingConfig || syncMutation.isPending}
                  onClick={() => syncMutation.mutate()}
                >
                  <RefreshCw className="mr-2 size-4" />
                  同步模型
                </Button>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
              <Input
                value={manualModelId}
                onChange={(event) => setManualModelId(event.target.value)}
                placeholder="手工模型 ID"
              />
              <Input
                value={manualModelName}
                onChange={(event) => setManualModelName(event.target.value)}
                placeholder="手工模型展示名（可选）"
              />
              <Button
                type="button"
                variant="secondary"
                onClick={addManualModel}
              >
                添加手工模型
              </Button>
            </div>

            <div className="space-y-3">
              {models.length === 0 ? (
                <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                  暂无模型。先保存配置，再执行连接校验或同步模型。
                </div>
              ) : (
                models.map((item) => (
                  <div
                    key={item.model_id}
                    className="flex flex-col gap-3 rounded-md border p-3 md:flex-row md:items-center md:justify-between"
                  >
                    <div className="space-y-1">
                      <div className="font-medium">{item.display_name}</div>
                      <div className="text-sm text-muted-foreground">
                        {item.model_id}
                      </div>
                      <div className="flex gap-2">
                        <Badge variant="outline">
                          {item.source_type === "manual" ? "手工" : "自动发现"}
                        </Badge>
                        <Badge variant="secondary">{item.sync_status}</Badge>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2">
                        <Checkbox
                          checked={item.is_enabled}
                          onCheckedChange={(checked) =>
                            toggleModelEnabled(item.model_id, checked === true)
                          }
                          id={`enable-${item.model_id}`}
                        />
                        <Label htmlFor={`enable-${item.model_id}`}>可用</Label>
                      </div>
                      {item.source_type === "manual" ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => removeManualModel(item.model_id)}
                        >
                          移除
                        </Button>
                      ) : null}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <div className="text-sm text-muted-foreground">
            {workingConfig?.validation_message ??
              "保存后可继续执行校验和模型同步。"}
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={saveMutation.isPending}
              onClick={() => setIsOpen(false)}
            >
              取消
            </Button>
            <LoadingButton
              type="button"
              loading={saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              保存
            </LoadingButton>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default ProviderConfigDialog
