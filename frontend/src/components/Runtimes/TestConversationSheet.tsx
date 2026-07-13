import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { type RuntimeEvent, releasesApi, tenantApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"

export default function TestConversationSheet({
  runtimeId,
}: {
  runtimeId: string
}) {
  const [sessionId, setSessionId] = useState<string>()
  const [prompt, setPrompt] = useState("")
  const [events, setEvents] = useState<RuntimeEvent[]>([])
  const [sending, setSending] = useState(false)
  const [bindingId, setBindingId] = useState("")
  const { data: bindings } = useQuery({
    queryKey: ["runtime-agents"],
    queryFn: releasesApi.runtimeAgents,
  })
  const streamController = useRef<AbortController | null>(null)

  useEffect(() => () => streamController.current?.abort(), [])

  async function send() {
    if (!prompt.trim()) return
    streamController.current?.abort()
    const controller = new AbortController()
    streamController.current = controller
    setSending(true)
    try {
      const activeSession =
        sessionId ?? (await tenantApi.createRuntimeSession(bindingId)).id
      setSessionId(activeSession)
      const task = await tenantApi.sendRuntimeMessage(
        activeSession,
        prompt.trim(),
      )
      setPrompt("")
      await tenantApi.streamRuntimeEvents(
        task.id,
        (event) => {
          if (!controller.signal.aborted) {
            setEvents((current) => [...current, event])
          }
        },
        controller.signal,
      )
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        throw error
      }
    } finally {
      if (streamController.current === controller) {
        setSending(false)
      }
    }
  }

  return (
    <Sheet
      onOpenChange={(open) => {
        if (!open) streamController.current?.abort()
      }}
    >
      <SheetTrigger asChild>
        <Button variant="outline">功能测试</Button>
      </SheetTrigger>
      <SheetContent className="flex w-full flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>平台运行时多轮对话测试</SheetTitle>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-auto py-4">
          {events.map((event, index) => (
            <div
              className="rounded-md border p-3 text-sm"
              key={`${event.sequence}-${index}`}
            >
              <div className="mb-1 font-medium">{event.event_type}</div>
              <pre className="whitespace-pre-wrap break-words text-xs">
                {JSON.stringify(event.payload, null, 2)}
              </pre>
            </div>
          ))}
        </div>
        <div className="space-y-2 border-t pt-4">
          <textarea
            className="min-h-24 w-full rounded-md border bg-background p-3 text-sm"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="输入测试消息"
          />
          {!sessionId && (
            <select
              className="w-full rounded-md border bg-background p-2"
              value={bindingId}
              onChange={(event) => setBindingId(event.target.value)}
            >
              <option value="">选择平台已激活的 Agent Release</option>
              {((bindings?.data ?? []) as Array<Record<string, string>>)
                .filter((item) => item.runtime_profile_id === runtimeId)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.agent_id} · {item.current_release_id}
                  </option>
                ))}
            </select>
          )}
          <Button
            className="w-full"
            disabled={sending || !prompt.trim() || (!sessionId && !bindingId)}
            onClick={send}
          >
            {sending ? "执行中…" : "发送"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  )
}
