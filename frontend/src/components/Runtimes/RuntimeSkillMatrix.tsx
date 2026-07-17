import { useQuery } from "@tanstack/react-query"
import { tenantApi } from "@/api/tenantApi"

export default function RuntimeSkillMatrix({ runtimeId }: { runtimeId: string }) {
  const query = useQuery({
    queryKey: ["runtime-skills", runtimeId],
    queryFn: () => tenantApi.readRuntimeSkills(runtimeId),
    refetchInterval: 5000,
  })
  if (!query.data?.count)
    return <p className="text-sm text-muted-foreground">尚无 Skill 同步订阅。</p>
  return (
    <div className="space-y-2">
      {query.data.data.map((state) => (
        <button
          type="button"
          key={String(state.id)}
          className="grid w-full gap-2 rounded border p-3 text-left text-sm md:grid-cols-4"
          onClick={() => window.location.assign(`/system/skills/${String(state.skill_id)}`)}
        >
          <span className="font-medium">{String(state.skill_slug)}</span>
          <span>期望 v{String(state.desired_version ?? "-")}</span>
          <span>已应用 v{String(state.applied_version ?? "-")}</span>
          <span className={state.status === "failed" || state.status === "blocked" ? "text-destructive" : ""}>{String(state.status)}</span>
        </button>
      ))}
    </div>
  )
}
