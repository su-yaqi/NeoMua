import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { type ReactNode, useState } from "react"
import type { ManagedIdentity } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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

interface IdentityApi {
  list: () => Promise<{ data: ManagedIdentity[]; count: number }>
  create: (body: {
    slug: string
    name: string
    description?: string
  }) => Promise<ManagedIdentity>
}

export default function IdentityManager({
  title,
  description,
  queryKey,
  api,
  canManage,
  createLabel,
  identifierLabel,
  createAction,
  onSelect,
}: {
  title: string
  description: string
  queryKey: string
  api: IdentityApi
  canManage: boolean
  createLabel: string
  identifierLabel: string
  createAction?: ReactNode
  onSelect?: (item: ManagedIdentity) => void
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const { data } = useQuery({ queryKey: [queryKey], queryFn: api.list })
  const create = useMutation({
    mutationFn: () => api.create({ slug, name }),
    onSuccess: () => {
      setSlug("")
      setName("")
      setIsCreateOpen(false)
      showSuccessToast(`${title}已创建`)
      queryClient.invalidateQueries({ queryKey: [queryKey] })
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
          <p className="text-muted-foreground">{description}</p>
        </div>
        {canManage && createAction}
        {canManage && !createAction && (
          <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
            <DialogTrigger asChild>
              <Button className="shrink-0">
                <Plus className="mr-2 size-4" />
                {createLabel}
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>{createLabel}</DialogTitle>
                <DialogDescription>
                  填写名称和{identifierLabel}，创建后可继续配置版本与能力。
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 py-4">
                <div className="space-y-2">
                  <Label htmlFor={`${queryKey}-name`}>名称</Label>
                  <Input
                    id={`${queryKey}-name`}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`${queryKey}-slug`}>{identifierLabel}</Label>
                  <Input
                    id={`${queryKey}-slug`}
                    value={slug}
                    onChange={(event) => setSlug(event.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    用于链接及系统引用，创建后不可修改；仅支持小写字母、数字和连字符。
                  </p>
                </div>
              </div>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="outline" disabled={create.isPending}>
                    取消
                  </Button>
                </DialogClose>
                <Button
                  disabled={!slug || !name || create.isPending}
                  onClick={() => create.mutate()}
                >
                  {create.isPending ? "创建中..." : "创建"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {data?.data.map((item) => (
          <Card
            key={item.id}
            className="cursor-pointer"
            onClick={() => onSelect?.(item)}
          >
            <CardHeader>
              <CardTitle className="text-base">{item.name}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">{identifierLabel}</p>
              <code>{item.slug}</code>
              <p className="text-sm text-muted-foreground">
                {item.archived ? "已归档" : "可用"}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
