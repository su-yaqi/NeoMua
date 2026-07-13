import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export const Route = createFileRoute(
  "/_layout/workspace/conversations/$conversationId",
)({ component: ConversationPage })

function ConversationPage() {
  const { conversationId } = Route.useParams()
  const queryClient = useQueryClient()
  const [content, setContent] = useState("")
  const [target, setTarget] = useState("main")
  const [attachment, setAttachment] = useState<File | null>(null)
  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => workspaceApi.getConversation(conversationId),
    refetchInterval: 3000,
  })
  const messages = useQuery({
    queryKey: ["conversation-messages", conversationId],
    queryFn: () => workspaceApi.listMessages(conversationId),
    refetchInterval: 2500,
  })
  const send = useMutation({
    mutationFn: async () => {
      const uploaded = attachment
        ? await workspaceApi.uploadConversationAttachment(
            conversationId,
            attachment,
          )
        : null
      return workspaceApi.sendMessage(conversationId, {
        content,
        target_type:
          conversation.data?.mode === "chat"
            ? "model"
            : target === "main" || target === "all"
              ? target
              : "agent",
        target_agent_id: target === "main" || target === "all" ? null : target,
        attachment_ids: uploaded ? [uploaded.id] : [],
      })
    },
    onSuccess: () => {
      setContent("")
      setAttachment(null)
      queryClient.invalidateQueries({
        queryKey: ["conversation-messages", conversationId],
      })
    },
    onError: () =>
      toast.error("消息发送失败；固定目标可能已失效或仍有在途轮次"),
  })
  const refresh = useMutation({
    mutationFn: () => workspaceApi.refreshContext(conversationId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["conversation", conversationId],
      }),
  })
  if (!conversation.data) return <p>正在加载会话…</p>
  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{conversation.data.title}</h1>
          <p className="text-muted-foreground">
            固定 Runtime {conversation.data.runtime_id.slice(0, 8)} ·{" "}
            {conversation.data.model_id ||
              `${conversation.data.agents.length} 个固定 Agent Release`}
          </p>
        </div>
        <div className="flex gap-2">
          <Badge>{conversation.data.mode}</Badge>
          {conversation.data.project_id && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => refresh.mutate()}
            >
              刷新项目快照
            </Button>
          )}
        </div>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>主线与圆桌记录</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {messages.data?.data.map((message) => (
            <div
              key={message.id}
              className={`rounded-lg border p-3 ${message.author_type === "user" ? "ml-10 bg-muted/40" : "mr-10"}`}
            >
              <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                <span>
                  {message.author_type} → {message.target_type}
                </span>
                <span>{message.status}</span>
              </div>
              <p className="whitespace-pre-wrap text-sm">
                {String(
                  message.payload.content ||
                    message.payload.result ||
                    JSON.stringify(message.payload),
                )}
              </p>
              {message.error && (
                <p className="mt-2 text-sm text-destructive">
                  {JSON.stringify(message.error)}
                </p>
              )}
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardContent className="grid gap-3 py-4 md:grid-cols-[180px_1fr_auto]">
          {conversation.data.mode === "agent" ? (
            <Select value={target} onValueChange={setTarget}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="main">主 Agent</SelectItem>
                <SelectItem value="all">全体</SelectItem>
                {conversation.data.agents
                  .filter((agent) => agent.role === "collaborator")
                  .map((agent) => (
                    <SelectItem key={agent.id} value={agent.id}>
                      协作 Agent {agent.agent_id.slice(0, 8)}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          ) : (
            <div className="flex items-center text-sm text-muted-foreground">
              固定模型
            </div>
          )}
          <Input
            placeholder="输入消息"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && content) send.mutate()
            }}
          />
          <Button
            disabled={!content || send.isPending}
            onClick={() => send.mutate()}
          >
            发送
          </Button>
          <div className="text-xs text-muted-foreground md:col-start-2">
            <input
              accept=".txt,.md,.markdown,.json,text/plain,text/markdown,application/json"
              type="file"
              onChange={(event) =>
                setAttachment(event.target.files?.item(0) ?? null)
              }
            />
            <span>
              仅接受经过严格文本扫描的 UTF-8 TXT/Markdown/JSON，最大 5 MiB。
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
