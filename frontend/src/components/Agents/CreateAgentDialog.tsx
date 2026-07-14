import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import {
  type AgentDefinition,
  agentsApi,
  harnessProfilesApi,
  tenantApi,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
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

export default function CreateAgentDialog({
  source,
}: {
  source?: AgentDefinition
}) {
  const [open, setOpen] = useState(false)
  const [slug, setSlug] = useState(source ? `${source.slug}-copy` : "")
  const [name, setName] = useState(source ? `${source.name} 副本` : "")
  const [description, setDescription] = useState(source?.description ?? "")
  const [harnessProfileId, setHarnessProfileId] = useState("")
  const [modelSelection, setModelSelection] = useState("")
  const [systemPrompt, setSystemPrompt] = useState("")
  const [timeoutSeconds, setTimeoutSeconds] = useState(3600)
  const [workingDirectoryStrategy, setWorkingDirectoryStrategy] =
    useState("inherit")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const profiles = useQuery({
    queryKey: ["harness-profiles"],
    queryFn: harnessProfilesApi.list,
    enabled: open && !source,
  })
  const providers = useQuery({
    queryKey: ["llm-provider-configs"],
    queryFn: tenantApi.readLlmProviderConfigs,
    enabled: open && !source,
  })

  const mutation = useMutation({
    mutationFn: async () => {
      if (source) return agentsApi.copy(source.id, { slug, name })
      const result = await agentsApi.createComplete({
        slug,
        name,
        description: description || undefined,
        harness_profile_id: harnessProfileId,
        provider_config_id: modelSelection.split("::")[0],
        model_id: modelSelection.split("::")[1],
        system_prompt: systemPrompt,
        config: {
          timeout_seconds: timeoutSeconds,
          working_directory_strategy: workingDirectoryStrategy,
        },
      })
      return result.agent
    },
    onSuccess: (agent) => {
      showSuccessToast(source ? "Agent 已复制" : "Agent 已创建")
      setOpen(false)
      setSlug("")
      setName("")
      setDescription("")
      setHarnessProfileId("")
      setModelSelection("")
      setSystemPrompt("")
      queryClient.invalidateQueries({ queryKey: ["agents"] })
      navigate({ to: "/system/agents/$agentId", params: { agentId: agent.id } })
    },
    onError: handleError.bind(showErrorToast),
  })

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setSlug(source ? `${source.slug}-copy` : "")
      setName(source ? `${source.name} 副本` : "")
      setDescription(source?.description ?? "")
      setHarnessProfileId("")
      setModelSelection("")
      setSystemPrompt("")
      setTimeoutSeconds(3600)
      setWorkingDirectoryStrategy("inherit")
    }
    setOpen(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          variant={source ? "outline" : "default"}
          size={source ? "sm" : "default"}
        >
          {source ? "复制" : "创建 Agent"}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{source ? "复制 Agent" : "创建 Agent"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor={`agent-slug-${source?.id ?? "new"}`}>
              Agent 标识
            </Label>
            <Input
              id={`agent-slug-${source?.id ?? "new"}`}
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="my-agent"
            />
            <p className="text-xs text-muted-foreground">
              用于链接及系统引用，空间内唯一且创建后不可修改；仅支持小写字母、数字和连字符。
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`agent-name-${source?.id ?? "new"}`}>名称</Label>
            <Input
              id={`agent-name-${source?.id ?? "new"}`}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          {!source && (
            <>
              <div className="space-y-2">
                <Label htmlFor="agent-desc">说明</Label>
                <Input
                  id="agent-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>Harness Profile</Label>
                <Select
                  value={harnessProfileId}
                  onValueChange={setHarnessProfileId}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择 Harness Profile" />
                  </SelectTrigger>
                  <SelectContent>
                    {profiles.data?.data
                      .filter((profile) => !profile.archived)
                      .map((profile) => (
                        <SelectItem key={profile.id} value={profile.id}>
                          {profile.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>模型</Label>
                <Select
                  value={modelSelection}
                  onValueChange={setModelSelection}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择已启用模型" />
                  </SelectTrigger>
                  <SelectContent>
                    {providers.data?.data
                      .filter((provider) => provider.enabled)
                      .flatMap((provider) =>
                        provider.models
                          .filter((model) => model.is_enabled)
                          .map((model) => (
                            <SelectItem
                              key={`${provider.id}::${model.model_id}`}
                              value={`${provider.id}::${model.model_id}`}
                            >
                              {provider.config_name} /{" "}
                              {model.display_name || model.model_id}
                            </SelectItem>
                          )),
                      )}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>系统提示词</Label>
                <textarea
                  className="min-h-32 w-full rounded-md border bg-background p-3 text-sm"
                  value={systemPrompt}
                  onChange={(event) => setSystemPrompt(event.target.value)}
                />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>超时（秒）</Label>
                  <Input
                    type="number"
                    min={1}
                    max={3600}
                    value={timeoutSeconds}
                    onChange={(event) =>
                      setTimeoutSeconds(Number(event.target.value))
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label>工作目录策略</Label>
                  <Select
                    value={workingDirectoryStrategy}
                    onValueChange={setWorkingDirectoryStrategy}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">继承运行时工作区</SelectItem>
                      <SelectItem value="require_root">
                        要求预登记根目录
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </>
          )}
        </div>
        <DialogFooter>
          <Button
            onClick={() => mutation.mutate()}
            disabled={
              !slug ||
              !name ||
              (!source && (!harnessProfileId || !modelSelection)) ||
              mutation.isPending
            }
          >
            {source ? "复制" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
