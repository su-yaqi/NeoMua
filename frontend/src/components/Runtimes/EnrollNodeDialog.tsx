import { useMutation } from "@tanstack/react-query"
import { useState } from "react"

import { tenantApi } from "@/api/tenantApi"
import { client } from "@/client/client.gen"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

export default function EnrollNodeDialog({
  mode,
}: {
  mode: "service" | "client"
}) {
  const [open, setOpen] = useState(false)
  const [token, setToken] = useState<string>()
  const [signingPublicKey, setSigningPublicKey] = useState<string>()
  const create = useMutation({
    mutationFn: () => tenantApi.createNodeBootstrapSession(mode),
    onSuccess: (result) => {
      setToken(result.token)
      setSigningPublicKey(result.signing_public_key)
    },
  })
  const title = mode === "service" ? "添加服务节点" : "添加客户端节点"
  const command =
    token && signingPublicKey
      ? `sudo neomua-node install --mode ${mode} --platform-url ${client.getConfig().baseURL || window.location.origin} --signing-public-key ${signingPublicKey}`
      : ""

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) {
          setToken(undefined)
          setSigningPublicKey(undefined)
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant={mode === "service" ? "default" : "outline"}>
          {title}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          请由管理员登录目标 Linux/systemd 节点后执行。NeoMua 不会通过 SSH
          登录该机器。
        </p>
        <p className="text-sm text-muted-foreground">
          {mode === "service"
            ? "服务模式部署受管 Claude Agent SDK 与管理组件。"
            : "客户端模式只安装管理端，不安装或修改本机 Claude Code、Codex。"}
        </p>
        {token && signingPublicKey ? (
          <div className="space-y-3">
            <section aria-label={`${title}命令`}>
              <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
                {command}
              </pre>
            </section>
            <section aria-label="一次性凭证">
              <p className="mb-1 text-xs text-muted-foreground">
                命令将以隐藏输入提示此凭证；关闭后不再显示。
              </p>
              <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
                {token}
              </pre>
            </section>
            <Button
              variant="outline"
              onClick={() => navigator.clipboard.writeText(command)}
            >
              复制安装命令
            </Button>
          </div>
        ) : (
          <Button onClick={() => create.mutate()} disabled={create.isPending}>
            生成一次性安装凭证
          </Button>
        )}
        {create.isError ? (
          <p className="text-sm text-destructive">{create.error.message}</p>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
