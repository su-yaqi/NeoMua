import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"
import {
  mcpServersApi,
  pluginsApi,
  skillsApi,
  tenantApi,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

function IdentifierField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="仅小写字母、数字和连字符"
      />
      <p className="text-xs text-muted-foreground">
        用于链接及系统引用，创建后不可修改。
      </p>
    </div>
  )
}

export function CreateSkillCompleteDialog() {
  const [open, setOpen] = useState(false)
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [version, setVersion] = useState("1.0.0")
  const [file, setFile] = useState<File | null>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const mutation = useMutation({
    mutationFn: () =>
      skillsApi.createComplete({
        slug,
        name,
        description: description || undefined,
        version,
        file: file!,
      }),
    onSuccess: ({ skill }) => {
      showSuccessToast("Skill 与首个不可变版本已创建")
      queryClient.invalidateQueries({ queryKey: ["skills"] })
      setOpen(false)
      navigate({ to: "/system/skills/$skillId", params: { skillId: skill.id } })
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 size-4" />
          新建 Skill
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>新建 Skill</DialogTitle>
          <DialogDescription>
            填写身份并上传首个版本；扫描失败不会留下空 Skill。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="space-y-2">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <IdentifierField label="Skill 标识" value={slug} onChange={setSlug} />
          <div className="space-y-2">
            <Label>说明</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>首个版本</Label>
            <Input
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>Skill ZIP</Label>
            <Input
              type="file"
              accept=".zip"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              取消
            </Button>
          </DialogClose>
          <Button
            disabled={!name || !slug || !version || !file || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "扫描并创建中..." : "扫描并创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function CreateMcpCompleteDialog() {
  const [open, setOpen] = useState(false)
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [transport, setTransport] = useState("streamable_http")
  const [endpoint, setEndpoint] = useState("")
  const [runtimeId, setRuntimeId] = useState("")
  const [secretRef, setSecretRef] = useState("")
  const [secretText, setSecretText] = useState("{}")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const platform = useQuery({
    queryKey: ["platform-runtime"],
    queryFn: tenantApi.readPlatformRuntime,
    enabled: open,
    retry: false,
  })
  const nodes = useQuery({
    queryKey: ["runtime-nodes"],
    queryFn: tenantApi.readRuntimeNodes,
    enabled: open,
  })
  const runtimes = useMemo(
    () => [
      ...(platform.data
        ? [{ id: platform.data.id, label: "平台运行时", kind: "platform" }]
        : []),
      ...(nodes.data?.data
        .filter((node) => node.runtime_profile_id)
        .map((node) => ({
          id: node.runtime_profile_id!,
          label: `${node.name}（节点）`,
          kind: "node",
        })) ?? []),
    ],
    [platform.data, nodes.data],
  )
  const selectedRuntime = runtimes.find((runtime) => runtime.id === runtimeId)
  const mutation = useMutation({
    mutationFn: async () => {
      let secretInputs: Record<string, string>
      try {
        secretInputs = JSON.parse(secretText) as Record<string, string>
      } catch {
        throw new Error("secret_json_invalid")
      }
      const result = await mcpServersApi.createComplete({
        slug,
        name,
        description: description || undefined,
        revision: {
          transport,
          config:
            transport === "stdio"
              ? { executable_key: endpoint, args: [] }
              : { endpoint },
        },
        target: {
          runtime_profile_id: runtimeId,
          ...(selectedRuntime?.kind === "node"
            ? { secret_ref: secretRef }
            : {}),
        },
        secret_inputs: selectedRuntime?.kind === "platform" ? secretInputs : {},
      })
      let validationStarted = true
      try {
        await mcpServersApi.validateTarget(result.target.id)
      } catch {
        validationStarted = false
      }
      return { result, validationStarted }
    },
    onSuccess: ({ result, validationStarted }) => {
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] })
      if (validationStarted) showSuccessToast("MCP 配置已创建并提交校验")
      else toast.error("MCP 配置已完整创建，但校验提交失败；请在详情中重试")
      setOpen(false)
      navigate({
        to: "/system/mcp-servers/$mcpServerId",
        params: { mcpServerId: result.server.id },
      })
    },
    onError: (error) => {
      if (error instanceof Error && error.message === "secret_json_invalid")
        showErrorToast("凭证必须是有效 JSON")
      else handleError.call(showErrorToast, error)
    },
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 size-4" />
          新建 MCP Server
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建 MCP Server</DialogTitle>
          <DialogDescription>
            一次完成身份、首个 Revision、运行时目标与凭证配置。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <IdentifierField label="MCP 标识" value={slug} onChange={setSlug} />
          <div className="space-y-2 md:col-span-2">
            <Label>说明</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>Transport</Label>
            <select
              className="h-10 w-full rounded-md border bg-background px-3"
              value={transport}
              onChange={(e) => setTransport(e.target.value)}
            >
              <option value="streamable_http">streamable_http</option>
              <option value="sse">sse</option>
              <option value="stdio">stdio</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label>
              {transport === "stdio" ? "Executable Key" : "Endpoint"}
            </Label>
            <Input
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>Runtime Target</Label>
            <select
              className="h-10 w-full rounded-md border bg-background px-3"
              value={runtimeId}
              onChange={(e) => setRuntimeId(e.target.value)}
            >
              <option value="">选择明确运行时</option>
              {runtimes.map((runtime) => (
                <option key={runtime.id} value={runtime.id}>
                  {runtime.label}
                </option>
              ))}
            </select>
          </div>
          {selectedRuntime?.kind === "node" ? (
            <div className="space-y-2">
              <Label>节点本地 secret_ref</Label>
              <Input
                value={secretRef}
                onChange={(e) => setSecretRef(e.target.value)}
              />
            </div>
          ) : (
            <div className="space-y-2">
              <Label>平台凭证 JSON（可选）</Label>
              <Input
                type="password"
                value={secretText}
                onChange={(e) => setSecretText(e.target.value)}
              />
            </div>
          )}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              取消
            </Button>
          </DialogClose>
          <Button
            disabled={
              !name ||
              !slug ||
              !endpoint ||
              !runtimeId ||
              (selectedRuntime?.kind === "node" && !secretRef) ||
              mutation.isPending
            }
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "创建中..." : "创建并校验"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function CreatePluginCompleteDialog() {
  const [open, setOpen] = useState(false)
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [componentsText, setComponentsText] = useState("[]")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const mutation = useMutation({
    mutationFn: () => {
      let components: Array<Record<string, unknown>>
      try {
        components = JSON.parse(componentsText) as Array<
          Record<string, unknown>
        >
      } catch {
        throw new Error("components_json_invalid")
      }
      if (!Array.isArray(components) || components.length === 0)
        throw new Error("components_empty")
      return pluginsApi.createComplete({
        slug,
        name,
        description: description || undefined,
        harness_type: "claude_code",
        adapter_schema_version: "1.0",
        adapter_config: {},
        components,
      })
    },
    onSuccess: ({ plugin }) => {
      showSuccessToast("Plugin 与已校验草稿已创建")
      queryClient.invalidateQueries({ queryKey: ["plugins"] })
      setOpen(false)
      navigate({
        to: "/system/plugins/$pluginId",
        params: { pluginId: plugin.id },
      })
    },
    onError: (error) => {
      if (error instanceof Error && error.message === "components_json_invalid")
        showErrorToast("Contributions 必须是有效 JSON")
      else if (error instanceof Error && error.message === "components_empty")
        showErrorToast("至少配置一个 Contribution")
      else handleError.call(showErrorToast, error)
    },
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 size-4" />
          新建 Plugin
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建 Plugin</DialogTitle>
          <DialogDescription>
            一次完成身份和声明式 Contributions；创建后仍需显式发布不可变版本。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="space-y-2">
            <Label>名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <IdentifierField
            label="Plugin 标识"
            value={slug}
            onChange={setSlug}
          />
          <div className="space-y-2">
            <Label>说明</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>声明式 Contributions JSON</Label>
            <textarea
              className="min-h-64 w-full rounded-md border bg-background p-3 font-mono text-xs"
              value={componentsText}
              onChange={(e) => setComponentsText(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              取消
            </Button>
          </DialogClose>
          <Button
            disabled={!name || !slug || !componentsText || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "校验并创建中..." : "校验并创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
