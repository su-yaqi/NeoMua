import { useMutation, useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useMemo, useState } from "react"
import { toast } from "sonner"
import { workspaceApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { WorkflowCreateProps } from "@/workflowRegistry"

const agentRoles = [
  ["requirements_communication", "需求沟通"],
  ["requirements_design", "需求设计"],
  ["technical_solution", "技术方案"],
  ["backend_development", "后端开发"],
  ["frontend_development", "前端开发"],
  ["test_case_design", "测试用例"],
  ["test_environment_deployment", "测试环境部署"],
  ["testing", "测试"],
] as const

export function WebPlatformDevelopmentCreate({
  workflowSlug,
  templateVersionId,
}: WorkflowCreateProps) {
  const navigate = useNavigate()
  const [projectId, setProjectId] = useState("")
  const [runtimeChoice, setRuntimeChoice] = useState("project-default")
  const [title, setTitle] = useState("")
  const [goal, setGoal] = useState("")
  const [description, setDescription] = useState("")
  const [agentBindings, setAgentBindings] = useState<Record<string, string>>({})
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => workspaceApi.listProjects(),
  })
  const runtimes = useQuery({
    queryKey: ["conversation-runtimes"],
    queryFn: workspaceApi.listConversationRuntimes,
  })
  const selectedProject = projects.data?.data.find(
    (project) => project.id === projectId,
  )
  const runtimeId =
    runtimeChoice === "project-default"
      ? selectedProject?.default_runtime_id || ""
      : runtimeChoice
  const agents = useQuery({
    queryKey: ["conversation-agents", runtimeId],
    queryFn: () => workspaceApi.listConversationAgents(runtimeId),
    enabled: Boolean(runtimeId),
  })
  const availableAgents = useMemo(
    () => agents.data?.data.filter((agent) => agent.active) || [],
    [agents.data],
  )
  const allRolesBound = agentRoles.every(([roleKey]) => agentBindings[roleKey])
  const createInstance = useMutation({
    mutationFn: () =>
      workspaceApi.createVisibleWorkflowInstance({
        title,
        template_version_id: templateVersionId,
        project_id: projectId,
        runtime_id: runtimeId,
        input: { goal, description },
        agent_bindings: agentBindings,
      }),
    onSuccess: (instance) =>
      navigate({
        to: "/apps/$workflowSlug/tasks/$instanceId",
        params: { workflowSlug, instanceId: instance.id },
      }),
    onError: () =>
      toast.error("创建前预检未通过，请检查项目、Runtime 与 Agent 发布版本"),
  })

  const selectProject = (nextProjectId: string) => {
    setProjectId(nextProjectId)
    setRuntimeChoice("project-default")
    setAgentBindings({})
  }
  const selectRuntime = (nextRuntime: string) => {
    setRuntimeChoice(nextRuntime)
    setAgentBindings({})
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">新建 Web 平台开发流程</h1>
        <p className="text-sm text-muted-foreground">
          项目和 Runtime 会在创建时固定；同一个 Agent 可以承担多个流程角色。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>基本信息</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>项目</Label>
            <Select value={projectId} onValueChange={selectProject}>
              <SelectTrigger>
                <SelectValue placeholder="选择项目" />
              </SelectTrigger>
              <SelectContent>
                {projects.data?.data.map((project) => (
                  <SelectItem key={project.id} value={project.id}>
                    {project.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Runtime</Label>
            <Select value={runtimeChoice} onValueChange={selectRuntime}>
              <SelectTrigger>
                <SelectValue placeholder="选择 Runtime" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="project-default">
                  使用项目默认 Runtime
                </SelectItem>
                {runtimes.data?.data
                  .filter((runtime) => runtime.compatible)
                  .map((runtime) => (
                    <SelectItem key={runtime.id} value={runtime.id}>
                      {runtime.runtime_type === "platform" ? "平台" : "节点"} ·{" "}
                      {runtime.model_id}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            {projectId && !runtimeId && (
              <p className="text-xs text-destructive">
                当前项目未配置默认 Runtime，请显式选择一个 Runtime。
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="workflow-title">流程标题</Label>
            <Input
              id="workflow-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：客户门户 0.7 版本开发"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="workflow-goal">研发目标</Label>
            <Input
              id="workflow-goal"
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="描述最终需要交付的结果"
            />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="workflow-description">补充说明</Label>
            <textarea
              id="workflow-description"
              className="min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="范围、限制、验收要求或其他背景"
            />
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Agent 角色配置</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          {agentRoles.map(([roleKey, roleName]) => (
            <div key={roleKey} className="space-y-2">
              <Label>{roleName}</Label>
              <Select
                value={agentBindings[roleKey] || ""}
                onValueChange={(releaseId) =>
                  setAgentBindings((current) => ({
                    ...current,
                    [roleKey]: releaseId,
                  }))
                }
                disabled={!runtimeId || agents.isPending}
              >
                <SelectTrigger>
                  <SelectValue
                    placeholder={runtimeId ? "选择 Agent" : "请先确定 Runtime"}
                  />
                </SelectTrigger>
                <SelectContent>
                  {availableAgents.map((agent) => (
                    <SelectItem key={agent.release_id} value={agent.release_id}>
                      {agent.agent_name} · v{agent.release_version}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
          {runtimeId && !agents.isPending && !availableAgents.length && (
            <p className="text-sm text-destructive md:col-span-2">
              该 Runtime 没有已激活的 Agent 发布版本，无法创建流程实例。
            </p>
          )}
          <div className="flex justify-end md:col-span-2">
            <Button
              disabled={
                !projectId ||
                !runtimeId ||
                !title.trim() ||
                !goal.trim() ||
                !allRolesBound ||
                createInstance.isPending
              }
              onClick={() => createInstance.mutate()}
            >
              预检并创建流程实例
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
