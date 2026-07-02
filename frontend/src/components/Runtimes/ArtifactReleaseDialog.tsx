import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { type RuntimeArtifact, tenantApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

export default function ArtifactReleaseDialog({
  artifact,
}: {
  artifact: RuntimeArtifact
}) {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const nodes = useQuery({
    queryKey: ["runtime-nodes"],
    queryFn: tenantApi.readRuntimeNodes,
  })
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const release = useMutation({
    mutationFn: () =>
      tenantApi.createArtifactRelease({
        artifact_id: artifact.id,
        node_ids: selected,
        valid_for_seconds: 3600,
      }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["artifact-releases"] })
      navigate({
        to: "/system/runtimes/releases/$releaseId",
        params: { releaseId: result.id },
      })
    },
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          发布内容
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>发布 {artifact.version}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          {nodes.data?.data
            .filter((node) => !node.revoked_at)
            .map((node) => (
              <label
                className="flex items-center gap-2 rounded-md border p-2"
                key={node.id}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(node.id)}
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, node.id]
                        : current.filter((id) => id !== node.id),
                    )
                  }
                />
                {node.name}
                <span className="ml-auto text-xs text-muted-foreground">
                  {node.online ? "在线" : "离线等待"}
                </span>
              </label>
            ))}
          <Button
            className="w-full"
            disabled={!selected.length || release.isPending}
            onClick={() => {
              if (window.confirm("确认创建一小时有效的签名发布？"))
                release.mutate()
            }}
          >
            确认发布
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
