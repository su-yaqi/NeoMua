import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { tenantApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const targetByKind: Record<string, string> = {
  agent: "agents",
  skill: "skills",
  mcp: "mcp",
  cli_config: "cli",
  workspace_content: "workspace",
}

export default function ArtifactUploadDialog() {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState("skill")
  const [version, setVersion] = useState("")
  const [file, setFile] = useState<File>()
  const queryClient = useQueryClient()
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set("kind", kind)
      form.set("logical_target", targetByKind[kind])
      form.set("version", version)
      if (file) form.set("file", file)
      return tenantApi.uploadRuntimeArtifact(form)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["runtime-artifacts"] })
      setOpen(false)
    },
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>上传内容</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>上传不可变内容包</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>内容类型</Label>
            <select
              className="mt-1 w-full rounded-md border bg-background p-2"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              <option value="agent">Agent</option>
              <option value="skill">Skill</option>
              <option value="mcp">MCP</option>
              <option value="cli_config">CLI 配置</option>
              <option value="workspace_content">工作区内容</option>
            </select>
          </div>
          <div>
            <Label>版本</Label>
            <Input
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            />
          </div>
          <div>
            <Label>ZIP 文件</Label>
            <Input
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => setFile(event.target.files?.[0])}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            只接受普通文件；绝对路径、目录穿越和符号链接会被拒绝，不执行任何安装钩子。
          </p>
          <Button
            className="w-full"
            disabled={!version || !file || upload.isPending}
            onClick={() => upload.mutate()}
          >
            上传并签名
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
