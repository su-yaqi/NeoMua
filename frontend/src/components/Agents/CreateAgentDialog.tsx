import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import {
  type AgentDefinition,
  agentsApi,
  modelDefinitionsApi,
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
  const [preferredModelDefinitionId, setPreferredModelDefinitionId] =
    useState("")
  const [systemPrompt, setSystemPrompt] = useState("")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const models = useQuery({
    queryKey: ["model-definitions"],
    queryFn: modelDefinitionsApi.list,
    enabled: open && !source,
  })

  const mutation = useMutation({
    mutationFn: async () => {
      if (source) return agentsApi.copy(source.id, { slug, name })
      const result = await agentsApi.createComplete({
        slug,
        name,
        description: description || undefined,
        preferred_model_definition_id: preferredModelDefinitionId,
        execution_policy: {
          permission_mode: "default",
          timeout_seconds: 3600,
          working_directory_strategy: "inherit",
        },
        system_prompt: systemPrompt,
        config: {},
      })
      return result.agent
    },
    onSuccess: (agent) => {
      showSuccessToast(source ? "Agent 已复制" : "Agent 已创建")
      setOpen(false)
      void queryClient.invalidateQueries({ queryKey: ["agents"] })
      navigate({ to: "/system/agents/$agentId", params: { agentId: agent.id } })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant={source ? "outline" : "default"}
          size={source ? "sm" : "default"}
        >
          {source ? "复制" : "创建 Agent"}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{source ? "复制 Agent" : "创建 Agent"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Agent 标识</Label>
            <Input
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>名称</Label>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          {!source && (
            <>
              <div className="space-y-2">
                <Label>说明</Label>
                <Input
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>模型偏好</Label>
                <Select
                  value={preferredModelDefinitionId}
                  onValueChange={setPreferredModelDefinitionId}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择稳定模型身份" />
                  </SelectTrigger>
                  <SelectContent>
                    {models.data?.data
                      .filter((item) => item.enabled)
                      .map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.display_name || item.model_key} ·{" "}
                          {item.provider_family}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  这里只声明偏好；实际 Runtime、路由与模型由会话或 Workflow
                  配置解析并冻结。
                </p>
              </div>
              <div className="space-y-2">
                <Label>系统提示词</Label>
                <textarea
                  className="min-h-32 w-full rounded-md border bg-background p-3 text-sm"
                  value={systemPrompt}
                  onChange={(event) => setSystemPrompt(event.target.value)}
                />
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
              (!source && !preferredModelDefinitionId) ||
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
