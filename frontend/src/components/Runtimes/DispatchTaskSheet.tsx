import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { type RuntimeNode, releasesApi, tenantApi } from "@/api/tenantApi"
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
  const [bindingId, setBindingId] = useState("")
  const { data: bindings } = useQuery({
    queryKey: ["runtime-agents"],
    queryFn: releasesApi.runtimeAgents,
  })
  const options = (
    (bindings?.data ?? []) as Array<Record<string, string>>
  ).filter((item) => item.runtime_profile_id === node.runtime_profile_id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const create = useMutation({
    mutationFn: () =>
      tenantApi.createRuntimeTask(
        {
          runtime_profile_id: node.runtime_profile_id,
          runtime_agent_release_id: bindingId,
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
          <select
            className="w-full rounded-md border bg-background p-2"
            value={bindingId}
            onChange={(event) => setBindingId(event.target.value)}
          >
            <option value="">选择该目标已激活的 Agent Release</option>
            {options.map((item) => (
              <option key={item.id} value={item.id}>
                {item.agent_id} · {item.current_release_id}
              </option>
            ))}
          </select>
          <Button
            className="w-full"
            disabled={!prompt.trim() || !bindingId || create.isPending}
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
