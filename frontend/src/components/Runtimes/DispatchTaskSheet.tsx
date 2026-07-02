import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { type RuntimeNode, tenantApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"

export default function DispatchTaskSheet({ node }: { node: RuntimeNode }) {
  const [prompt, setPrompt] = useState("")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const create = useMutation({
    mutationFn: () =>
      tenantApi.createRuntimeTask(
        {
          runtime_profile_id: node.runtime_profile_id,
          node_id: node.id,
          prompt,
          task_kind: "ordinary",
        },
        crypto.randomUUID(),
      ),
    onSuccess: async (task) => {
      await queryClient.invalidateQueries({ queryKey: ["runtime-tasks"] })
      navigate({
        to: "/system/runtimes/tasks/$taskId",
        params: { taskId: task.id },
      })
    },
  })
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button size="sm" disabled={!node.online || !node.runtime_profile_id}>
          下发任务
        </Button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>向 {node.name} 下发普通任务</SheetTitle>
        </SheetHeader>
        <div className="space-y-3 py-4">
          <textarea
            className="min-h-40 w-full rounded-md border bg-background p-3"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="描述需要 Agent 完成的工作"
          />
          <Button
            className="w-full"
            disabled={!prompt.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            确认下发
          </Button>
          {create.isError ? (
            <p className="text-sm text-destructive">{create.error.message}</p>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
