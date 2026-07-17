import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  File,
  FilePlus2,
  Folder,
  History,
  Pencil,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { skillsApi, type SkillDraftFile } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/system/skills/$skillId")({
  component: Page,
})

function MarkdownPreview({ source }: { source: string }) {
  const lines = source.split("\n")
  return (
    <div className="space-y-2 text-sm leading-7">
      {lines.map((line, index) => {
        const key = `${index}-${line.slice(0, 16)}`
        if (line.startsWith("### "))
          return <h3 className="text-base font-semibold" key={key}>{line.slice(4)}</h3>
        if (line.startsWith("## "))
          return <h2 className="text-lg font-semibold" key={key}>{line.slice(3)}</h2>
        if (line.startsWith("# "))
          return <h1 className="text-xl font-bold" key={key}>{line.slice(2)}</h1>
        if (line.startsWith("- "))
          return <p className="pl-4" key={key}>• {line.slice(2)}</p>
        if (line.startsWith("> "))
          return <blockquote className="border-l-2 pl-3 text-muted-foreground" key={key}>{line.slice(2)}</blockquote>
        if (line.startsWith("```"))
          return <div className="font-mono text-xs text-muted-foreground" key={key}>{line}</div>
        return <p className={line ? "" : "h-3"} key={key}>{line}</p>
      })}
    </div>
  )
}

function fileDepth(path: string) {
  return Math.max(0, path.split("/").length - 1)
}

function VersionFiles({ skillId, version }: { skillId: string; version: string }) {
  const [path, setPath] = useState<string | null>(null)
  const files = useQuery({
    queryKey: ["skill-version-files", skillId, version],
    queryFn: () => skillsApi.versionFiles(skillId, version),
  })
  const content = useQuery({
    queryKey: ["skill-version-file", skillId, version, path],
    queryFn: () => skillsApi.versionFile(skillId, version, path as string),
    enabled: Boolean(path),
  })
  return (
    <div className="mt-3 grid overflow-hidden rounded border md:grid-cols-[240px_1fr]">
      <div className="border-r bg-muted/20 p-2">
        {files.data?.data.map((file) => (
          <button
            type="button"
            key={file.path}
            className="block w-full truncate rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
            style={{ paddingLeft: `${8 + fileDepth(file.path) * 12}px` }}
            onClick={() => setPath(file.path)}
          >
            {file.path}
          </button>
        ))}
      </div>
      <div className="max-h-80 overflow-auto p-3">
        {content.data?.is_text ? (
          path?.endsWith(".md") ? (
            <MarkdownPreview source={content.data.content ?? ""} />
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-xs">{content.data.content}</pre>
          )
        ) : (
          <p className="text-xs text-muted-foreground">选择文本文件查看只读内容。</p>
        )}
      </div>
    </div>
  )
}

function Page() {
  const { skillId } = Route.useParams()
  const { user } = useAuth()
  const client = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === localStorage.getItem("selected_namespace_id"),
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  const [section, setSection] = useState<"workspace" | "versions" | "sync">("workspace")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [content, setContent] = useState("")
  const [mode, setMode] = useState<"edit" | "preview" | "split">("edit")
  const [publishVersion, setPublishVersion] = useState("")
  const { data } = useQuery({
    queryKey: ["skill", skillId],
    queryFn: () => skillsApi.get(skillId),
  })
  const { data: sync } = useQuery({
    queryKey: ["skill-sync", skillId],
    queryFn: () => skillsApi.runtimeSync(skillId),
    refetchInterval: section === "sync" ? 5000 : false,
  })
  const files = useMemo(
    () => [...(data?.draft.files ?? [])].sort((a, b) => a.path.localeCompare(b.path)),
    [data?.draft.files],
  )
  useEffect(() => {
    if (!selectedId && files.length) {
      setSelectedId(files.find((item) => item.path === "SKILL.md")?.id ?? files[0].id)
    }
  }, [files, selectedId])
  const selected = files.find((item) => item.id === selectedId) ?? null
  const { data: selectedContent } = useQuery({
    queryKey: ["skill-draft-file", skillId, selectedId],
    queryFn: () => skillsApi.draftFile(skillId, selectedId as string),
    enabled: Boolean(selectedId),
  })
  useEffect(() => setContent(selectedContent?.content ?? ""), [selectedContent])
  const dirty = Boolean(selected?.is_text) && content !== (selectedContent?.content ?? "")
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [dirty])
  const selectFile = (fileId: string) => {
    if (dirty && !window.confirm("当前文件有未保存修改，确认放弃并切换？")) return
    setSelectedId(fileId)
  }

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["skill", skillId] })
    await client.invalidateQueries({ queryKey: ["skill-draft-file", skillId] })
  }
  const save = useMutation({
    mutationFn: () =>
      skillsApi.saveDraftFile(skillId, selectedId as string, {
        expected_revision: data?.draft.revision ?? 0,
        content,
      }),
    onSuccess: async () => {
      showSuccessToast("草稿文件已保存")
      await refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const validate = useMutation({
    mutationFn: () => skillsApi.validateDraft(skillId, data?.draft.revision ?? 0),
    onSuccess: async () => {
      showSuccessToast("当前草稿已通过校验")
      await refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const publish = useMutation({
    mutationFn: () =>
      skillsApi.publishDraft(skillId, {
        expected_revision: data?.draft.revision ?? 0,
        version: publishVersion,
      }),
    onSuccess: async () => {
      showSuccessToast("新版本已发布，运行时同步已独立排队")
      setPublishVersion("")
      await refresh()
      await client.invalidateQueries({ queryKey: ["skill-sync", skillId] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const addFile = async () => {
    const path = window.prompt("新文件路径，例如 references/guide.md")
    if (!path || !data) return
    const result = await skillsApi.createDraftFile(skillId, {
      expected_revision: data.draft.revision,
      path,
      content: "",
    })
    setSelectedId(result.file.id)
    await refresh()
  }
  const renameFile = async (file: SkillDraftFile) => {
    const path = window.prompt("新路径", file.path)
    if (!path || path === file.path || !data) return
    await skillsApi.moveDraftFile(skillId, file.id, {
      expected_revision: data.draft.revision,
      path,
    })
    await refresh()
  }
  const deleteFile = async (file: SkillDraftFile) => {
    if (!data || !window.confirm(`删除 ${file.path}？`)) return
    await skillsApi.deleteDraftFile(skillId, file.id, data.draft.revision)
    setSelectedId(null)
    await refresh()
  }

  if (!data) return <p>加载 Skill 工作区…</p>
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Skill / {data.slug}</p>
          <h1 className="text-2xl font-bold">{data.name}</h1>
          <p className="text-sm text-muted-foreground">
            当前 v{data.current_version.version} · 草稿 r{data.draft.revision}
          </p>
        </div>
        <div className="flex gap-2">
          {(["workspace", "versions", "sync"] as const).map((item) => (
            <Button
              key={item}
              variant={section === item ? "default" : "outline"}
              onClick={() => setSection(item)}
            >
              {item === "workspace" ? "工作区" : item === "versions" ? "版本" : "同步"}
            </Button>
          ))}
        </div>
      </div>

      {section === "workspace" && (
        <div className="grid min-h-[650px] overflow-hidden rounded-lg border lg:grid-cols-[280px_1fr]">
          <aside className="border-b bg-muted/20 lg:border-r lg:border-b-0">
            <div className="flex items-center justify-between border-b px-3 py-2">
              <span className="text-sm font-medium">文件</span>
              {canManage && (
                <span className="flex">
                  <label className="cursor-pointer rounded px-2 py-1 text-xs hover:bg-muted">
                    导入 ZIP
                    <input
                      className="hidden"
                      type="file"
                      accept=".zip"
                      onChange={async (event) => {
                        const file = event.target.files?.[0]
                        const version = file ? window.prompt("ZIP 内 SKILL.md 的版本号") : null
                        if (file && version) {
                          await skillsApi.importDraft(
                            skillId,
                            version,
                            data.draft.revision,
                            file,
                          )
                          setSelectedId(null)
                          await refresh()
                        }
                        event.target.value = ""
                      }}
                    />
                  </label>
                  <Button size="sm" variant="ghost" onClick={addFile}><FilePlus2 className="size-4" /></Button>
                </span>
              )}
            </div>
            <div className="p-2">
              {files.map((file) => (
                <div
                  key={file.id}
                  className={`group flex items-center gap-2 rounded px-2 py-2 text-sm ${selectedId === file.id ? "bg-primary/10 text-primary" : "hover:bg-muted"}`}
                  style={{ paddingLeft: `${8 + fileDepth(file.path) * 14}px` }}
                >
                  {fileDepth(file.path) ? <Folder className="size-3.5" /> : <File className="size-3.5" />}
                  <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => selectFile(file.id)}>{file.path}</button>
                  {canManage && file.path !== "SKILL.md" && (
                    <span className="hidden gap-1 group-hover:flex">
                      <button type="button" onClick={() => renameFile(file)}><Pencil className="size-3" /></button>
                      <button type="button" onClick={() => deleteFile(file)}><Trash2 className="size-3" /></button>
                    </span>
                  )}
                </div>
              ))}
            </div>
          </aside>
          <main className="flex min-w-0 flex-col">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
              <code className="text-xs">{selected?.path ?? "请选择文件"}</code>
              <div className="flex gap-2">
                {selected?.path.endsWith(".md") && (
                  <span className="flex gap-1">
                    {(["edit", "preview", "split"] as const).map((item) => (
                      <Button
                        key={item}
                        size="sm"
                        variant={mode === item ? "default" : "outline"}
                        onClick={() => setMode(item)}
                      >
                        {item === "edit" ? "编辑" : item === "preview" ? "预览" : "并排"}
                      </Button>
                    ))}
                  </span>
                )}
                {canManage && selected?.is_text && mode !== "preview" && (
                  <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}><Save className="mr-1 size-4" />保存</Button>
                )}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-4">
              {!selected?.is_text ? (
                <p className="text-sm text-muted-foreground">二进制文件仅展示元数据，不支持在线编辑。</p>
              ) : mode === "preview" ? (
                <MarkdownPreview source={content} />
              ) : mode === "split" ? (
                <div className="grid min-h-[520px] gap-4 md:grid-cols-2">
                  <textarea
                    className="min-h-[520px] w-full resize-none border-r bg-transparent pr-4 font-mono text-sm outline-none"
                    value={content}
                    readOnly={!canManage}
                    spellCheck={false}
                    onChange={(event) => setContent(event.target.value)}
                  />
                  <div className="overflow-auto"><MarkdownPreview source={content} /></div>
                </div>
              ) : (
                <textarea
                  className="min-h-[520px] w-full resize-none bg-transparent font-mono text-sm outline-none"
                  value={content}
                  readOnly={!canManage}
                  spellCheck={false}
                  onChange={(event) => setContent(event.target.value)}
                />
              )}
            </div>
            {canManage && (
              <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/20 p-3">
                <div className="text-xs text-muted-foreground">
                  {data.draft.validated_revision === data.draft.revision ? "已验证，可发布" : "内容变更后需要重新验证"}
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" disabled={validate.isPending} onClick={() => validate.mutate()}>验证草稿</Button>
                  <Input className="w-32" placeholder="1.2.0" value={publishVersion} onChange={(event) => setPublishVersion(event.target.value)} />
                  <Button disabled={!publishVersion || data.draft.validated_revision !== data.draft.revision || publish.isPending} onClick={() => publish.mutate()}>发布版本</Button>
                </div>
              </div>
            )}
          </main>
        </div>
      )}

      {section === "versions" && (
        <div className="space-y-3">
          {data.versions.map((version) => (
            <Card key={version.id}>
              <CardContent className="p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3"><History className="size-4" /><div><p className="font-medium">v{version.version}{version.id === data.current_version_id ? " · 当前" : ""}</p><code className="text-xs text-muted-foreground">{version.content_sha256.slice(0, 16)}</code></div></div>
                  {canManage && version.id !== data.current_version_id && !version.deprecated && <Button variant="outline" onClick={async () => { if (window.confirm(`确认将当前版本切换到 v${version.version}？`)) { await skillsApi.setCurrentVersion(skillId, version.id); await refresh() } }}>设为当前版本</Button>}
                </div>
                <VersionFiles skillId={skillId} version={version.version} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {section === "sync" && (
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><RefreshCw className="size-4" />运行时同步</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            {!sync?.count && <p className="text-sm text-muted-foreground">尚无运行时订阅此 Skill。</p>}
            {sync?.data.map((state) => (
              <div key={String(state.id)} className="grid gap-2 rounded border p-3 text-sm md:grid-cols-4">
                <span>Runtime {String(state.runtime_instance_id || state.runtime_profile_id).slice(0, 8)}</span>
                <span>期望 v{String(state.desired_version ?? "-")}</span>
                <span>已应用 v{String(state.applied_version ?? "-")}</span>
                <span className={state.status === "failed" || state.status === "blocked" ? "text-destructive" : ""}>
                  {String(state.status)}
                  {canManage && state.status === "failed" ? (
                    <Button
                      className="ml-2"
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        await skillsApi.retryRuntimeSync(
                          skillId,
                          String(state.runtime_instance_id || state.runtime_profile_id),
                        )
                        await client.invalidateQueries({ queryKey: ["skill-sync", skillId] })
                      }}
                    >
                      重试
                    </Button>
                  ) : null}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
