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

export default function EnrollNodeDialog() {
  const [open, setOpen] = useState(false)
  const [token, setToken] = useState<string>()
  const create = useMutation({
    mutationFn: tenantApi.createNodeEnrollmentToken,
    onSuccess: (result) => setToken(result.token),
  })
  const command = token
    ? `sudo neomua-node install --platform-url ${client.getConfig().baseURL || window.location.origin} --enrollment-token '${token}'`
    : ""

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) setToken(undefined)
      }}
    >
      <DialogTrigger asChild>
        <Button onClick={() => create.mutate()}>安装新节点</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>安装节点运行时</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          先通过组织的软件分发安装
          neomua-node，再在目标设备执行以下一次性命令。令牌关闭后不再显示。
        </p>
        {token ? (
          <div className="space-y-3">
            <section aria-label="安装命令">
              <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
                {command}
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
          <p className="text-sm">正在生成一次性凭证…</p>
        )}
        {create.isError ? (
          <p className="text-sm text-destructive">{create.error.message}</p>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
