import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { Bot, MessageSquare, Paperclip, Plus, Send } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"
import {
  type ConversationAgentCatalogItem,
  type ConversationSummary,
  workspaceApi,
} from "@/api/tenantApi"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

type WorkspaceMode = "chat" | "agent"

export default function AIWorkspace({
  conversationId,
}: {
  conversationId?: string
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [content, setContent] = useState("")
  const [attachment, setAttachment] = useState<File | null>(null)
  const [mode, setMode] = useState<WorkspaceMode>("chat")
  const [runtimeId, setRuntimeId] = useState("")
  const [projectId, setProjectId] = useState("none")
  const [modelKey, setModelKey] = useState("")
  const [agentBindingIds, setAgentBindingIds] = useState<string[]>([])
  const [organizerBindingId, setOrganizerBindingId] = useState("")
  const [target, setTarget] = useState("main")
  const [configurationDirty, setConfigurationDirty] = useState(false)
  const [streamState, setStreamState] = useState<
    "connecting" | "connected" | "reconnecting" | "closed"
  >(conversationId ? "connecting" : "closed")
  const lastEventId = useRef(0)

  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: workspaceApi.listConversations,
  })
  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => workspaceApi.getConversation(conversationId || ""),
    enabled: Boolean(conversationId),
    refetchInterval: conversationId ? 3000 : false,
  })
  const messages = useQuery({
    queryKey: ["conversation-messages", conversationId],
    queryFn: () => workspaceApi.listMessages(conversationId || ""),
    enabled: Boolean(conversationId),
  })
  const delegations = useQuery({
    queryKey: ["conversation-delegations", conversationId],
    queryFn: () => workspaceApi.listDelegations(conversationId || ""),
    enabled: Boolean(conversationId && conversation.data?.mode === "agent"),
  })
  const runtimes = useQuery({
    queryKey: ["conversation-runtimes"],
    queryFn: workspaceApi.listConversationRuntimes,
  })
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => workspaceApi.listProjects(),
  })
  const effectiveRuntimeId = conversation.data?.runtime_id || runtimeId
  const effectiveMode = conversation.data?.mode || mode
  const models = useQuery({
    queryKey: ["conversation-models", effectiveRuntimeId],
    queryFn: () => workspaceApi.listConversationModels(effectiveRuntimeId),
    enabled: Boolean(effectiveRuntimeId && effectiveMode === "chat"),
  })
  const agents = useQuery({
    queryKey: ["conversation-agents", effectiveRuntimeId],
    queryFn: () => workspaceApi.listConversationAgents(effectiveRuntimeId),
    enabled: Boolean(effectiveRuntimeId && effectiveMode === "agent"),
  })

  useEffect(() => {
    const value = conversation.data
    if (!value) return
    setMode(value.mode)
    setRuntimeId(value.runtime_id)
    setProjectId(value.project_id || "none")
    setModelKey(
      value.provider_config_id && value.model_id
        ? `${value.provider_config_id}:${value.model_id}`
        : "",
    )
    const activeAgents = value.agents.filter((item) => item.active)
    setAgentBindingIds(
      activeAgents.map((item) => item.runtime_agent_release_id),
    )
    const organizer = activeAgents.find((item) => item.role === "main")
    setOrganizerBindingId(organizer?.runtime_agent_release_id || "")
    setConfigurationDirty(false)
  }, [conversation.data])

  useEffect(() => {
    if (!conversationId || conversation.data?.status !== "active") {
      setStreamState("closed")
      return
    }
    const controller = new AbortController()
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const connect = async () => {
      setStreamState(lastEventId.current ? "reconnecting" : "connecting")
      try {
        setStreamState("connected")
        await workspaceApi.streamConversationEvents(
          conversationId,
          lastEventId.current,
          (event) => {
            lastEventId.current = Math.max(lastEventId.current, event.sequence)
            void queryClient.invalidateQueries({
              queryKey: ["conversation-messages", conversationId],
            })
            void queryClient.invalidateQueries({
              queryKey: ["conversation-delegations", conversationId],
            })
            if (event.event_type === "configuration_changed") {
              void queryClient.invalidateQueries({
                queryKey: ["conversation", conversationId],
              })
            }
          },
          controller.signal,
        )
      } catch (error) {
        if (!controller.signal.aborted) {
          setStreamState("reconnecting")
          console.error("Conversation event stream disconnected", error)
        }
      }
      if (!controller.signal.aborted) {
        retryTimer = setTimeout(() => void connect(), 1000)
      }
    }
    void connect()
    return () => {
      controller.abort()
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [conversation.data?.status, conversationId, queryClient])

  const selectedModel = useMemo(
    () =>
      models.data?.data.find(
        (item) => `${item.provider_config_id}:${item.model_id}` === modelKey,
      ),
    [modelKey, models.data],
  )

  const createAndSend = useMutation({
    mutationFn: async () => {
      const title = content.trim().replace(/\s+/g, " ").slice(0, 48)
      const created = await workspaceApi.createConversation({
        title,
        mode,
        runtime_id: runtimeId,
        project_id: projectId === "none" ? null : projectId,
        visibility: "private",
        provider_config_id:
          mode === "chat" ? selectedModel?.provider_config_id : null,
        model_id: mode === "chat" ? selectedModel?.model_id : null,
        main_agent:
          mode === "agent"
            ? { runtime_agent_release_id: organizerBindingId }
            : null,
        collaborators:
          mode === "agent"
            ? agentBindingIds
                .filter((id) => id !== organizerBindingId)
                .map((id) => ({ runtime_agent_release_id: id }))
            : [],
      })
      const uploaded = attachment
        ? await workspaceApi.uploadConversationAttachment(
            created.id,
            attachment,
          )
        : null
      await workspaceApi.sendMessage(created.id, {
        content,
        target_type: mode === "chat" ? "model" : "main",
        target_agent_id: null,
        attachment_ids: uploaded ? [uploaded.id] : [],
      })
      return created
    },
    onSuccess: (created) => {
      setContent("")
      setAttachment(null)
      void queryClient.invalidateQueries({ queryKey: ["conversations"] })
      navigate({
        to: "/workspace/conversations/$conversationId",
        params: { conversationId: created.id },
      })
    },
    onError: () => toast.error("会话创建或首条消息发送失败；已保留可重试状态"),
  })

  const send = useMutation({
    mutationFn: async () => {
      if (!conversationId || !conversation.data) return
      const uploaded = attachment
        ? await workspaceApi.uploadConversationAttachment(
            conversationId,
            attachment,
          )
        : null
      await workspaceApi.sendMessage(conversationId, {
        content,
        target_type:
          conversation.data.mode === "chat"
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
      void queryClient.invalidateQueries({
        queryKey: ["conversation-messages", conversationId],
      })
      void queryClient.invalidateQueries({ queryKey: ["conversations"] })
    },
    onError: () =>
      toast.error("消息发送失败；请检查当前配置或是否仍有在途轮次"),
  })

  const updateConfiguration = useMutation({
    mutationFn: async () => {
      if (!conversationId || !conversation.data?.configuration) return
      const expected_revision = conversation.data.configuration.revision
      if (conversation.data.mode === "chat") {
        if (!selectedModel) throw new Error("model_required")
        return workspaceApi.updateConversationConfiguration(conversationId, {
          expected_revision,
          provider_config_id: selectedModel.provider_config_id,
          model_id: selectedModel.model_id,
        })
      }
      return workspaceApi.updateConversationConfiguration(conversationId, {
        expected_revision,
        participant_runtime_agent_release_ids: agentBindingIds,
        organizer_runtime_agent_release_id: organizerBindingId,
      })
    },
    onSuccess: () => {
      setConfigurationDirty(false)
      void queryClient.invalidateQueries({
        queryKey: ["conversation", conversationId],
      })
      toast.success("会话配置已更新，后续消息将使用新配置")
    },
    onError: () =>
      toast.error("配置更新失败；有在途轮次、目标失效或修订发生冲突"),
  })

  const canCreate = Boolean(
    content.trim() &&
      runtimeId &&
      (mode === "chat"
        ? selectedModel
        : agentBindingIds.length > 0 &&
          organizerBindingId &&
          agentBindingIds.includes(organizerBindingId)),
  )
  const canSend = Boolean(
    content.trim() &&
      conversation.data?.status === "active" &&
      !configurationDirty,
  )

  return (
    <div className="flex h-full min-h-0 w-full overflow-hidden bg-background">
      <aside className="flex min-h-0 w-72 shrink-0 flex-col border-r bg-muted/20">
        <div className="border-b p-3">
          <Button asChild className="w-full justify-start">
            <Link to="/workspace">
              <Plus /> 新会话
            </Link>
          </Button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto p-2">
          {conversations.isError && (
            <p className="p-3 text-sm text-destructive">会话列表加载失败。</p>
          )}
          {conversations.data?.data.map((item) => (
            <ConversationListItem
              key={item.id}
              item={item}
              selected={item.id === conversationId}
            />
          ))}
          {conversations.data?.data.length === 0 && (
            <p className="p-3 text-sm text-muted-foreground">暂无历史会话。</p>
          )}
        </div>
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex min-h-16 items-center justify-between gap-3 border-b px-5 py-3">
          <div className="min-w-0">
            <h1 className="truncate font-semibold">
              {conversation.data?.title || "新会话"}
            </h1>
            <p className="truncate text-xs text-muted-foreground">
              {conversation.data
                ? `${conversation.data.mode === "chat" ? "Chat" : "Agent"} · Runtime ${conversation.data.runtime_id.slice(0, 8)}`
                : "选择项目、Runtime 与模型或 Agent 后开始对话"}
            </p>
          </div>
          {conversation.data && (
            <div className="flex items-center gap-2">
              <Badge variant="outline">
                修订 {conversation.data.configuration?.revision || 1}
              </Badge>
              <Badge variant="outline">事件流 {streamState}</Badge>
            </div>
          )}
        </header>

        <section className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {!conversationId ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted-foreground">
              <MessageSquare className="mb-4 size-10" />
              <p className="text-lg font-medium text-foreground">
                开始一个新对话
              </p>
              <p className="mt-1 text-sm">
                Chat 使用模型，Agent 支持多人协作与组织 Agent。
              </p>
            </div>
          ) : messages.isError ? (
            <p className="text-sm text-destructive">
              历史消息加载失败，请稍后重试。
            </p>
          ) : (
            <div className="mx-auto max-w-4xl space-y-4">
              {messages.data?.data.map((message) => (
                <div
                  key={message.id}
                  className={`flex ${message.author_type === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[82%] rounded-2xl px-4 py-3 text-sm ${
                      message.author_type === "user"
                        ? "bg-primary text-primary-foreground"
                        : "border bg-muted/35"
                    }`}
                  >
                    <div className="mb-1 flex items-center gap-2 text-[11px] opacity-70">
                      <span>{message.author_type}</span>
                      <span>·</span>
                      <span>{message.status}</span>
                      {message.configuration_revision_id && (
                        <span>
                          · 配置 {message.configuration_revision_id.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    <p className="whitespace-pre-wrap">
                      {String(
                        message.payload.content ||
                          message.payload.result ||
                          JSON.stringify(message.payload),
                      )}
                    </p>
                    {message.error && (
                      <p className="mt-2 text-xs text-destructive">
                        {JSON.stringify(message.error)}
                      </p>
                    )}
                  </div>
                </div>
              ))}
              {messages.data?.data.length === 0 && (
                <p className="py-12 text-center text-sm text-muted-foreground">
                  还没有消息。
                </p>
              )}
              {conversation.data?.mode === "agent" &&
                Boolean(delegations.data?.data.length) && (
                  <details className="rounded-lg border p-3 text-sm">
                    <summary className="cursor-pointer font-medium">
                      完整 Agent 委派记录（{delegations.data?.data.length}）
                    </summary>
                    <div className="mt-3 space-y-2">
                      {delegations.data?.data.map((delegation) => (
                        <div
                          key={delegation.id}
                          className="space-y-2 rounded border p-3"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline">
                              Delegation {delegation.id}
                            </Badge>
                            {delegation.task_id && (
                              <Badge variant="outline">
                                Agent Task {delegation.task_id}
                              </Badge>
                            )}
                            <Badge>{delegation.status}</Badge>
                          </div>
                          <p className="text-muted-foreground">
                            {delegation.source_agent_id.slice(0, 8)} →{" "}
                            {delegation.target_agent_id.slice(0, 8)}
                          </p>
                          <div className="grid gap-2 md:grid-cols-2">
                            <div>
                              <p className="mb-1 text-xs font-medium">
                                委派输入
                              </p>
                              <pre className="overflow-auto rounded bg-muted p-2 text-xs">
                                {JSON.stringify(
                                  delegation.input_payload,
                                  null,
                                  2,
                                )}
                              </pre>
                            </div>
                            <div>
                              <p className="mb-1 text-xs font-medium">
                                委派结果
                              </p>
                              <pre className="overflow-auto rounded bg-muted p-2 text-xs">
                                {JSON.stringify(
                                  delegation.result_payload ||
                                    delegation.error || { status: "等待结果" },
                                  null,
                                  2,
                                )}
                              </pre>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
            </div>
          )}
        </section>

        <footer className="border-t bg-background p-4">
          <div className="mx-auto max-w-4xl rounded-2xl border bg-muted/15 p-3 shadow-sm">
            <textarea
              className="min-h-20 w-full resize-none bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground"
              placeholder="输入消息…"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault()
                  if (conversationId ? canSend : canCreate) {
                    conversationId ? send.mutate() : createAndSend.mutate()
                  }
                }
              }}
            />
            <div className="flex items-center justify-between gap-3 border-t pt-3">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                {!conversationId && (
                  <div className="flex rounded-md border p-0.5">
                    <Button
                      size="sm"
                      variant={mode === "chat" ? "secondary" : "ghost"}
                      onClick={() => setMode("chat")}
                    >
                      Chat
                    </Button>
                    <Button
                      size="sm"
                      variant={mode === "agent" ? "secondary" : "ghost"}
                      onClick={() => setMode("agent")}
                    >
                      Agent
                    </Button>
                  </div>
                )}
                <ProjectSelect
                  value={projectId}
                  disabled={Boolean(conversationId)}
                  projects={projects.data?.data || []}
                  onChange={setProjectId}
                />
                <RuntimeSelect
                  value={effectiveRuntimeId}
                  disabled={Boolean(conversationId)}
                  runtimes={runtimes.data?.data || []}
                  onChange={(value) => {
                    setRuntimeId(value)
                    setModelKey("")
                    setAgentBindingIds([])
                    setOrganizerBindingId("")
                  }}
                />
                {effectiveMode === "chat" ? (
                  <ModelSelect
                    value={modelKey}
                    models={models.data?.data || []}
                    onChange={(value) => {
                      setModelKey(value)
                      if (conversationId) setConfigurationDirty(true)
                    }}
                  />
                ) : (
                  <AgentPicker
                    agents={agents.data?.data || []}
                    selected={agentBindingIds}
                    organizer={organizerBindingId}
                    onSelectedChange={(value) => {
                      setAgentBindingIds(value)
                      if (!value.includes(organizerBindingId)) {
                        setOrganizerBindingId(value[0] || "")
                      }
                      if (conversationId) setConfigurationDirty(true)
                    }}
                    onOrganizerChange={(value) => {
                      setOrganizerBindingId(value)
                      if (conversationId) setConfigurationDirty(true)
                    }}
                  />
                )}
                {conversation.data?.mode === "agent" && (
                  <Select value={target} onValueChange={setTarget}>
                    <SelectTrigger size="sm">
                      <SelectValue placeholder="消息目标" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="main">组织 Agent</SelectItem>
                      <SelectItem value="all">全体 Agent</SelectItem>
                      {conversation.data.agents
                        .filter((item) => item.active && item.role !== "main")
                        .map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            Agent {item.agent_id.slice(0, 8)}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                )}
                <label className="inline-flex h-8 cursor-pointer items-center gap-1 rounded-md border px-2 text-xs">
                  <Paperclip className="size-3.5" />
                  {attachment ? attachment.name : "附件"}
                  <input
                    className="hidden"
                    type="file"
                    accept=".txt,.md,.markdown,.json,text/plain,text/markdown,application/json"
                    onChange={(event) =>
                      setAttachment(event.target.files?.item(0) || null)
                    }
                  />
                </label>
                {conversationId && configurationDirty && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={updateConfiguration.isPending}
                    onClick={() => updateConfiguration.mutate()}
                  >
                    应用配置
                  </Button>
                )}
              </div>
              <Button
                size="icon"
                disabled={
                  conversationId
                    ? !canSend || send.isPending
                    : !canCreate || createAndSend.isPending
                }
                onClick={() =>
                  conversationId ? send.mutate() : createAndSend.mutate()
                }
              >
                <Send />
              </Button>
            </div>
          </div>
        </footer>
      </main>
    </div>
  )
}

function ConversationListItem({
  item,
  selected,
}: {
  item: ConversationSummary
  selected: boolean
}) {
  return (
    <Link
      to="/workspace/conversations/$conversationId"
      params={{ conversationId: item.id }}
      className={`block rounded-lg px-3 py-2.5 transition-colors ${
        selected ? "bg-accent" : "hover:bg-accent/60"
      }`}
    >
      <div className="flex items-center gap-2">
        {item.mode === "chat" ? (
          <MessageSquare className="size-4 shrink-0" />
        ) : (
          <Bot className="size-4 shrink-0" />
        )}
        <span className="truncate text-sm font-medium">{item.title}</span>
      </div>
      <p className="mt-1 truncate pl-6 text-xs text-muted-foreground">
        {item.mode === "chat"
          ? item.model_id
          : `${item.agents.filter((agent) => agent.active).length} 个 Agent`}
      </p>
    </Link>
  )
}

function ProjectSelect({
  value,
  disabled,
  projects,
  onChange,
}: {
  value: string
  disabled: boolean
  projects: Array<{ id: string; name: string }>
  onChange: (value: string) => void
}) {
  return (
    <Select value={value} disabled={disabled} onValueChange={onChange}>
      <SelectTrigger size="sm">
        <SelectValue placeholder="项目（可选）" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="none">无项目</SelectItem>
        {projects.map((project) => (
          <SelectItem key={project.id} value={project.id}>
            {project.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function RuntimeSelect({
  value,
  disabled,
  runtimes,
  onChange,
}: {
  value: string
  disabled: boolean
  runtimes: Array<{ id: string; runtime_type: string; compatible: boolean }>
  onChange: (value: string) => void
}) {
  return (
    <Select value={value} disabled={disabled} onValueChange={onChange}>
      <SelectTrigger size="sm">
        <SelectValue placeholder="选择 Runtime" />
      </SelectTrigger>
      <SelectContent>
        {runtimes.map((runtime) => (
          <SelectItem
            key={runtime.id}
            value={runtime.id}
            disabled={!runtime.compatible}
          >
            {runtime.runtime_type} / {runtime.id.slice(0, 8)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function ModelSelect({
  value,
  models,
  onChange,
}: {
  value: string
  models: Array<{
    provider_config_id: string
    provider_name: string
    model_id: string
    display_name: string | null
  }>
  onChange: (value: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm">
        <SelectValue placeholder="选择模型" />
      </SelectTrigger>
      <SelectContent>
        {models.map((model) => (
          <SelectItem
            key={`${model.provider_config_id}:${model.model_id}`}
            value={`${model.provider_config_id}:${model.model_id}`}
          >
            {model.provider_name} / {model.display_name || model.model_id}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function AgentPicker({
  agents,
  selected,
  organizer,
  onSelectedChange,
  onOrganizerChange,
}: {
  agents: ConversationAgentCatalogItem[]
  selected: string[]
  organizer: string
  onSelectedChange: (value: string[]) => void
  onOrganizerChange: (value: string) => void
}) {
  const activeAgents = agents.filter((agent) => agent.active)
  return (
    <details className="relative">
      <summary className="flex h-8 cursor-pointer list-none items-center rounded-md border px-3 text-xs">
        {selected.length ? `${selected.length} 个 Agent` : "选择 Agent"}
      </summary>
      <div className="absolute bottom-10 left-0 z-40 w-80 space-y-3 rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg">
        <div className="max-h-48 space-y-2 overflow-y-auto">
          {activeAgents.map((agent) => (
            <label
              key={agent.runtime_agent_release_id}
              className="flex cursor-pointer items-center gap-2 text-sm"
            >
              <input
                type="checkbox"
                checked={selected.includes(agent.runtime_agent_release_id)}
                onChange={(event) =>
                  onSelectedChange(
                    event.target.checked
                      ? [...selected, agent.runtime_agent_release_id]
                      : selected.filter(
                          (value) => value !== agent.runtime_agent_release_id,
                        ),
                  )
                }
              />
              <span className="truncate">
                {agent.agent_name} / {agent.release_version}
              </span>
            </label>
          ))}
        </div>
        <Select
          value={organizer}
          disabled={!selected.length}
          onValueChange={onOrganizerChange}
        >
          <SelectTrigger className="w-full" size="sm">
            <SelectValue placeholder="指定组织 Agent" />
          </SelectTrigger>
          <SelectContent>
            {activeAgents
              .filter((agent) =>
                selected.includes(agent.runtime_agent_release_id),
              )
              .map((agent) => (
                <SelectItem
                  key={agent.runtime_agent_release_id}
                  value={agent.runtime_agent_release_id}
                >
                  组织：{agent.agent_name}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </div>
    </details>
  )
}
