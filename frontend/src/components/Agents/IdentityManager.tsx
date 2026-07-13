import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import type { ManagedIdentity } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  onSelect,
}: {
  title: string
  description: string
  queryKey: string
  api: IdentityApi
  canManage: boolean
  onSelect?: (item: ManagedIdentity) => void
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const { data } = useQuery({ queryKey: [queryKey], queryFn: api.list })
  const create = useMutation({
    mutationFn: () => api.create({ slug, name }),
    onSuccess: () => {
      setSlug("")
      setName("")
      showSuccessToast(`${title}已创建`)
      queryClient.invalidateQueries({ queryKey: [queryKey] })
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        <p className="text-muted-foreground">{description}</p>
      </div>
      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>创建</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
            <div>
              <Label>Slug</Label>
              <Input
                value={slug}
                onChange={(event) => setSlug(event.target.value)}
              />
            </div>
            <div>
              <Label>名称</Label>
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <Button
              className="self-end"
              disabled={!slug || !name || create.isPending}
              onClick={() => create.mutate()}
            >
              创建
            </Button>
          </CardContent>
        </Card>
      )}
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
