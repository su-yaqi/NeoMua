import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useMemo, useState } from "react"
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export const Route = createFileRoute("/_layout/workspace")({
  component: WorkspacePage,
})

function WorkspacePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<"chat" | "agent">("chat")
  const [title, setTitle] = useState("")
  const [runtimeId, setRuntimeId] = useState("")
  const [modelKey, setModelKey] = useState("")
  const [mainAgentId, setMainAgentId] = useState("")
  const [collaborators, setCollaborators] = useState<string[]>([])
  const [projectId, setProjectId] = useState("none")
  const [visibility, setVisibility] = useState<"private" | "project">("private")
  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: workspaceApi.listConversations,
  })
  const runtimes = useQuery({
    queryKey: ["conversation-runtimes"],
    queryFn: workspaceApi.listConversationRuntimes,
  })
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => workspaceApi.listProjects(),
  })
  const models = useQuery({
    queryKey: ["conversation-models", runtimeId],
    queryFn: () => workspaceApi.listConversationModels(runtimeId),
    enabled: Boolean(runtimeId && mode === "chat"),
  })
  const agents = useQuery({
    queryKey: ["conversation-agents", runtimeId],
    queryFn: () => workspaceApi.listConversationAgents(runtimeId),
    enabled: Boolean(runtimeId && mode === "agent"),
  })
  const workflows = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: workspaceApi.listWorkflowTemplates,
  })
  const selectedModel = useMemo(
    () =>
      models.data?.data.find(
        (item) => `${item.provider_config_id}:${item.model_id}` === modelKey,
      ),
    [modelKey, models.data],
  )
  const createConversation = useMutation({
    mutationFn: () =>
      workspaceApi.createConversation({
        title,
        mode,
        runtime_id: runtimeId,
        project_id: projectId === "none" ? null : projectId,
        visibility: projectId === "none" ? "private" : visibility,
        provider_config_id:
          mode === "chat" ? selectedModel?.provider_config_id : null,
        model_id: mode === "chat" ? selectedModel?.model_id : null,
        main_agent:
          mode === "agent" ? { runtime_agent_release_id: mainAgentId } : null,
        collaborators:
          mode === "agent"
            ? collaborators.map((id) => ({ runtime_agent_release_id: id }))
            : [],
      }),
    onSuccess: (conversation) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] })
      navigate({
        to: "/workspace/conversations/$conversationId",
        params: { conversationId: conversation.id },
      })
    },
    onError: () =>
      toast.error("会话创建失败；请检查 Runtime、模型/Agent 和项目上下文状态"),
  })
  const canCreate = Boolean(
    title && runtimeId && (mode === "chat" ? selectedModel : mainAgentId),
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">AI 工作台</h1>
        <p className="text-muted-foreground">
          Chat 使用固定纯模型路由；Agent 使用固定 Release 圆桌；Workflow
          进入独立流程应用。
        </p>
      </div>
      <Tabs
        defaultValue="chat"
        onValueChange={(value) =>
          value !== "workflow" && setMode(value as "chat" | "agent")
        }
      >
        <TabsList>
          <TabsTrigger value="chat">Chat</TabsTrigger>
          <TabsTrigger value="agent">Agent</TabsTrigger>
          <TabsTrigger value="workflow">Workflow</TabsTrigger>
        </TabsList>
        <TabsContent value="chat">
          <ConversationCreator
            mode="chat"
            {...{
              title,
              setTitle,
              runtimeId,
              setRuntimeId,
              runtimes: runtimes.data?.data || [],
              projectId,
              setProjectId,
              projects: projects.data?.data || [],
              visibility,
              setVisibility,
            }}
          >
            <Select value={modelKey} onValueChange={setModelKey}>
              <SelectTrigger>
                <SelectValue placeholder="选择固定模型" />
              </SelectTrigger>
              <SelectContent>
                {models.data?.data.map((model) => (
                  <SelectItem
                    key={`${model.provider_config_id}:${model.model_id}`}
                    value={`${model.provider_config_id}:${model.model_id}`}
                  >
                    {model.provider_name} /{" "}
                    {model.display_name || model.model_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              disabled={!canCreate || createConversation.isPending}
              onClick={() => createConversation.mutate()}
            >
              创建 Chat
            </Button>
          </ConversationCreator>
        </TabsContent>
        <TabsContent value="agent">
          <ConversationCreator
            mode="agent"
            {...{
              title,
              setTitle,
              runtimeId,
              setRuntimeId,
              runtimes: runtimes.data?.data || [],
              projectId,
              setProjectId,
              projects: projects.data?.data || [],
              visibility,
              setVisibility,
            }}
          >
            <Select
              value={mainAgentId}
              onValueChange={(value) => {
                setMainAgentId(value)
                setCollaborators((items) => items.filter((id) => id !== value))
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择主 Agent" />
              </SelectTrigger>
              <SelectContent>
                {agents.data?.data
                  .filter((agent) => agent.active)
                  .map((agent) => (
                    <SelectItem
                      key={agent.runtime_agent_release_id}
                      value={agent.runtime_agent_release_id}
                    >
                      {agent.agent_name} / {agent.release_version}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <div className="space-y-2 rounded-md border p-3">
              <p className="text-sm font-medium">协作 Agent（可选）</p>
              {agents.data?.data
                .filter(
                  (agent) =>
                    agent.active &&
                    agent.runtime_agent_release_id !== mainAgentId,
                )
                .map((agent) => (
                  <label
                    key={agent.runtime_agent_release_id}
                    className="flex items-center gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={collaborators.includes(
                        agent.runtime_agent_release_id,
                      )}
                      onChange={(event) =>
                        setCollaborators((items) =>
                          event.target.checked
                            ? [...items, agent.runtime_agent_release_id]
                            : items.filter(
                                (id) => id !== agent.runtime_agent_release_id,
                              ),
                        )
                      }
                    />
                    {agent.agent_name} / {agent.release_version}
                  </label>
                ))}
            </div>
            <Button
              disabled={!canCreate || createConversation.isPending}
              onClick={() => createConversation.mutate()}
            >
              创建 Agent 圆桌
            </Button>
          </ConversationCreator>
        </TabsContent>
        <TabsContent value="workflow">
          <div className="grid gap-4 md:grid-cols-2">
            {workflows.data?.data.map((workflow) => (
              <Link
                key={workflow.id}
                to="/apps/$workflowSlug"
                params={{
                  workflowSlug:
                    workflow.application?.route_slug || workflow.slug,
                }}
              >
                <Card className="h-full hover:border-primary">
                  <CardHeader>
                    <CardTitle>{workflow.name}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground">
                      {workflow.description}
                    </p>
                    <p className="mt-3 text-sm">
                      {
                        workflow.versions.filter(
                          (item) => item.enablement.enabled,
                        ).length
                      }{" "}
                      个已启用版本
                    </p>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </TabsContent>
      </Tabs>
      <div>
        <h2 className="mb-3 text-lg font-semibold">最近会话</h2>
        <div className="space-y-2">
          {conversations.data?.data.map((conversation) => (
            <Link
              key={conversation.id}
              to="/workspace/conversations/$conversationId"
              params={{ conversationId: conversation.id }}
            >
              <Card className="hover:border-primary">
                <CardContent className="flex items-center justify-between py-4">
                  <div>
                    <p className="font-medium">{conversation.title}</p>
                    <p className="text-sm text-muted-foreground">
                      {conversation.model_id ||
                        `${conversation.agents.length} 个 Agent`}
                    </p>
                  </div>
                  <Badge>{conversation.mode}</Badge>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}

type CreatorProps = {
  mode: "chat" | "agent"
  title: string
  setTitle: (value: string) => void
  runtimeId: string
  setRuntimeId: (value: string) => void
  runtimes: Array<{ id: string; runtime_type: string; compatible: boolean }>
  projectId: string
  setProjectId: (value: string) => void
  projects: Array<{ id: string; name: string }>
  visibility: "private" | "project"
  setVisibility: (value: "private" | "project") => void
  children: React.ReactNode
}

function ConversationCreator({
  mode,
  title,
  setTitle,
  runtimeId,
  setRuntimeId,
  runtimes,
  projectId,
  setProjectId,
  projects,
  visibility,
  setVisibility,
  children,
}: CreatorProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>新建 {mode === "chat" ? "Chat" : "Agent 圆桌"}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-2">
        <Input
          placeholder="会话标题"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
        <Select value={runtimeId} onValueChange={setRuntimeId}>
          <SelectTrigger>
            <SelectValue placeholder="选择 Runtime" />
          </SelectTrigger>
          <SelectContent>
            {runtimes.map((runtime) => (
              <SelectItem key={runtime.id} value={runtime.id}>
                {runtime.runtime_type} / {runtime.id.slice(0, 8)}
                {runtime.compatible ? "" : "（未验证）"}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={projectId}
          onValueChange={(value) => {
            setProjectId(value)
            if (value === "none") setVisibility("private")
          }}
        >
          <SelectTrigger>
            <SelectValue placeholder="项目背景（可选）" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">不关联项目</SelectItem>
            {projects.map((project) => (
              <SelectItem key={project.id} value={project.id}>
                {project.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {projectId !== "none" && (
          <Select
            value={visibility}
            onValueChange={(value) =>
              setVisibility(value as "private" | "project")
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="private">个人可见</SelectItem>
              <SelectItem value="project">项目共享</SelectItem>
            </SelectContent>
          </Select>
        )}
        <div className="space-y-3 md:col-span-2">{children}</div>
      </CardContent>
    </Card>
  )
}
