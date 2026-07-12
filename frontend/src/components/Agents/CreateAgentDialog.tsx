import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { agentsApi } from "@/api/tenantApi"
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
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function CreateAgentDialog() {
  const [open, setOpen] = useState(false)
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: () =>
      agentsApi.create({ slug, name, description: description || undefined }),
    onSuccess: (agent) => {
      showSuccessToast("Agent 已创建")
      setOpen(false)
      setSlug("")
      setName("")
      setDescription("")
      queryClient.invalidateQueries({ queryKey: ["agents"] })
      navigate({ to: "/system/agents/$agentId", params: { agentId: agent.id } })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>创建 Agent</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>创建 Agent</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="agent-slug">Slug</Label>
            <Input
              id="agent-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="my-agent"
            />
            <p className="text-xs text-muted-foreground">
              空间内唯一，创建后不可修改。仅小写字母、数字、连字符。
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="agent-name">名称</Label>
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="agent-desc">说明</Label>
            <Input
              id="agent-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!slug || !name || mutation.isPending}
          >
            创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
